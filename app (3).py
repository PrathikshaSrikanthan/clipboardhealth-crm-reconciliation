"""Bellhaven CRM review app. Run: streamlit run app.py

Read-only by default. API writes require explicit reviewer approval and enabling
CRM_WRITE_ENABLED=1. The source CSVs are snapshots, not live account records.
"""
import csv
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
API = 'https://analyst-assessment-production.up.railway.app/api/v1'
DB = ROOT / 'review_decisions.sqlite3'
PARENT_ID = '0015QAPLGS3FVYEEEM'  # Confirm against live CRM before use.


def rows(filename):
    path = ROOT / filename
    if not path.exists():
        st.error(f'Missing file: {filename}. Put it next to app.py.')
        st.stop()
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def normalize(text):
    return ' '.join(str(text or '').lower().replace('street', 'st').replace('road', 'rd').replace('avenue', 'ave').split())


def money(value):
    try:
        return float(str(value or '0').replace(',', '').replace('$', ''))
    except ValueError:
        return 0.0


def requires_chow(account):
    """Protected CHOW requires BOTH historical revenue and outstanding AR."""
    return money(account.get('lifetime_revenue')) > 0 and money(account.get('outstanding_ar')) > 0


def account_id(account):
    return account.get('account_id') or account.get('id')


def account_candidates(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ('accounts', 'items', 'data', 'results'):
            value = body.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for nested in ('accounts', 'items', 'results'):
                    if isinstance(value.get(nested), list):
                        return value[nested]
    raise ValueError('CRM search response shape unknown; no account will be created')


def live_account(identifier):
    account = extract_account(api_request('GET', f'/accounts/{identifier}'))
    if account_id(account) != identifier:
        raise ValueError('Could not verify live CRM account identity')
    return account


def matching_live_accounts(payload):
    query = requests.utils.quote(payload['name'])
    candidates = account_candidates(api_request('GET', '/accounts?q=' + query))
    return [a for a in candidates
            if normalize(a.get('name')) == normalize(payload['name'])
            and normalize(a.get('billing_street')) == normalize(payload.get('billing_street'))
            and str(a.get('billing_zip') or '') == str(payload.get('billing_zip') or '')
            and a.get('parent_id') == payload.get('parent_id')]


def chow_record(db, old_id):
    return db.execute('SELECT old_account_id, new_account_id, status, create_payload '
                      'FROM chow_operations WHERE old_account_id=?', (old_id,)).fetchone()


def chow_transfer(db, old_id, new_parent_id, create_payload):
    """Reviewer-triggered, recoverable CHOW. Never repeat POST after an uncertain attempt."""
    old = live_account(old_id)
    if old.get('parent_id') == new_parent_id:
        raise ValueError('Old account already has that parent; no ownership change needed')
    if not requires_chow(old):
        response = api_request('PATCH', f'/accounts/{old_id}', {'parent_id': new_parent_id})
        verified = live_account(old_id)
        if verified.get('parent_id') != new_parent_id:
            raise RuntimeError('PATCH returned but new parent was not verified; inspect CRM')
        return {'mode': 'direct_reparent', 'response': response, 'new_parent_id': new_parent_id}

    existing_link = old.get('chow_current_account')
    if existing_link:
        linked = live_account(str(existing_link))
        if linked.get('parent_id') == new_parent_id:
            return {'mode': 'already_linked', 'new_account_id': str(existing_link)}
        raise ValueError('Historical account already has a CHOW link; inspect before proceeding')

    if create_payload.get('parent_id') != new_parent_id:
        raise ValueError('New account parent must match the selected destination parent')
    if not create_payload.get('name') or not create_payload.get('billing_street'):
        raise ValueError('New account requires verified name and physical street address')
    allowed = {'name', 'parent_id', 'billing_street', 'billing_city',
               'billing_state', 'billing_zip', 'status'}
    if set(create_payload) - allowed:
        raise ValueError('CHOW creation payload includes unsupported or historical fields')
    if create_payload.get('status') != 'Active':
        raise ValueError('New CHOW account must be Active')

    record = chow_record(db, old_id)
    if record:
        _, new_id, status, saved_payload = record
        if json.loads(saved_payload) != create_payload:
            raise ValueError('Existing CHOW operation has different details; inspect before retry')
        if not new_id:
            raise ValueError('A prior POST may have succeeded. Do NOT retry automatically. '
                             'Inspect CRM, then use the recovery section below.')
    else:
        matches = matching_live_accounts(create_payload)
        if matches:
            raise ValueError('Matching live account already exists. Verify its identity and '
                             'use the recovery section to link it; no POST made.')
        db.execute('INSERT INTO chow_operations '
                   '(old_account_id,new_account_id,status,create_payload) VALUES (?,?,?,?)',
                   (old_id, None, 'POST_PENDING', json.dumps(create_payload, sort_keys=True)))
        db.commit()  # Durable before network POST: uncertain outcomes must not be retried.
        response = api_request('POST', '/accounts', create_payload)
        created = extract_account(response)
        new_id = account_id(created)
        if not new_id:
            raise RuntimeError('POST may have succeeded but returned no identifiable account. '
                               'Inspect CRM and recover manually; do not retry POST.')
        db.execute('UPDATE chow_operations SET new_account_id=?,status=? '
                   'WHERE old_account_id=?', (new_id, 'CREATED', old_id))
        db.commit()

    new = live_account(str(new_id))
    if new.get('parent_id') != new_parent_id:
        raise ValueError('New account parent mismatch; historical account not linked')
    if normalize(new.get('name')) != normalize(create_payload['name']):
        raise ValueError('New account name mismatch; historical account not linked')
    if normalize(new.get('billing_street')) != normalize(create_payload['billing_street']):
        raise ValueError('New account address mismatch; historical account not linked')
    old_again = live_account(old_id)
    if old_again.get('chow_current_account'):
        if str(old_again['chow_current_account']) != str(new_id):
            raise ValueError('Old account linked to a different new account; no PATCH')
    else:
        if not requires_chow(old_again) or old_again.get('parent_id') != old.get('parent_id'):
            raise ValueError('Historical account changed since review; no PATCH')
        api_request('PATCH', f'/accounts/{old_id}', {'chow_current_account': str(new_id)})
    confirmed = live_account(old_id)
    if str(confirmed.get('chow_current_account')) != str(new_id):
        raise RuntimeError('CHOW link not verified; inspect CRM before retry')
    if confirmed.get('parent_id') != old.get('parent_id'):
        raise RuntimeError('Historical parent changed unexpectedly; inspect CRM')
    db.execute('UPDATE chow_operations SET status=? WHERE old_account_id=?',
               ('COMPLETE', old_id))
    db.commit()
    return {'mode': 'protected_chow', 'old_account_id': old_id,
            'new_account_id': str(new_id), 'verified': True}


def recover_chow(db, old_id, new_id):
    """Recover uncertain POST by linking a reviewer-verified existing account."""
    record = chow_record(db, old_id)
    if not record:
        raise ValueError('No pending CHOW operation exists for this historical account')
    _, stored_id, status, saved_payload = record
    payload = json.loads(saved_payload)
    if stored_id and str(stored_id) != new_id:
        raise ValueError('A different new account ID is already stored; no changes made')
    old = live_account(old_id)
    new = live_account(new_id)
    if not requires_chow(old):
        raise ValueError('Historical financials no longer require protected CHOW')
    if old.get('parent_id') == new.get('parent_id'):
        raise ValueError('New account has the same parent as the historical account')
    for field in ('parent_id', 'name', 'billing_street', 'billing_zip'):
        left, right = new.get(field), payload.get(field)
        if field in ('name', 'billing_street'):
            valid = normalize(left) == normalize(right)
        else:
            valid = str(left or '') == str(right or '')
        if not valid:
            raise ValueError(f'New account {field} differs from the approved creation payload')
    if old.get('chow_current_account') and str(old['chow_current_account']) != new_id:
        raise ValueError('Old account already linked to another account')
    db.execute('UPDATE chow_operations SET new_account_id=?, status=? '
               'WHERE old_account_id=?', (new_id, 'CREATED', old_id))
    db.commit()
    return chow_transfer(db, old_id, payload['parent_id'], payload)


def signature(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]


def connection():
    db = sqlite3.connect(DB)
    db.execute('''CREATE TABLE IF NOT EXISTS decisions (
      proposal_id TEXT PRIMARY KEY, decision TEXT NOT NULL,
      action TEXT NOT NULL, payload TEXT NOT NULL,
      response TEXT, decided_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    db.execute('''CREATE TABLE IF NOT EXISTS chow_operations (
      old_account_id TEXT PRIMARY KEY, new_account_id TEXT,
      status TEXT NOT NULL, create_payload TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    db.commit()
    return db


def decided(db):
    return {r[0]: r[1] for r in db.execute('SELECT proposal_id, decision FROM decisions')}


def api_request(method, path, payload=None):
    token = os.getenv('CRM_API_TOKEN', '').strip()
    if not token:
        raise RuntimeError('CRM_API_TOKEN is not configured in .env')
    response = requests.request(method, API + path,
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
        json=payload, timeout=25)
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if not response.ok:
        raise RuntimeError(f'CRM API {response.status_code}: {str(body)[:700]}')
    return body


def extract_account(body):
    if isinstance(body, dict):
        for key in ('account', 'data', 'result'):
            if isinstance(body.get(key), dict):
                return body[key]
        return body
    return {}


def build_proposals(matches, duplicates, facilities, accounts):
    by_id = {a['account_id']: a for a in accounts}
    by_name = {f['name']: f for f in facilities}
    proposals = []
    for match in matches:
        facility = by_name.get(match['facility_name'], {})
        account = by_id.get(match['crm_account_id'], {})
        if not facility:
            continue
        score = float(match.get('match_score') or 0)
        reasons = []
        if score < 85:
            reasons.append('Uncertain match or possible missing account')
        if account and account.get('parent_id') != PARENT_ID and score >= 85:
            reasons.append('Matched CRM account has a different parent')
        if account and normalize(facility.get('name')) != normalize(account.get('name')):
            reasons.append('Facility and CRM names differ')
        if account and normalize(facility.get('address')) != normalize(account.get('billing_street')):
            reasons.append('Website and CRM street addresses differ')
        if not reasons:
            continue
        proposal = {
            'kind': 'Facility reconciliation', 'facility': facility, 'account': account,
            'reason': '; '.join(reasons), 'score': score,
            'suggested_action': 'Investigate',
        }
        proposal['id'] = signature({'kind': proposal['kind'], 'facility': facility['name'], 'account': account.get('account_id')})
        proposals.append(proposal)

    groups = {}
    for d in duplicates:
        groups.setdefault(d['facility_name'], []).append(d)
    for name, candidates in groups.items():
        if len(candidates) < 2:
            continue
        proposal = {'kind': 'Duplicate candidates', 'facility': by_name.get(name, {}),
            'account': by_id.get(candidates[0]['crm_account_id'], {}),
            'candidates': [by_id.get(c['crm_account_id'], {}) for c in candidates],
            'reason': 'Multiple CRM accounts match the same facility. Verify history and ownership before inactivating a record.',
            'score': max(float(c['match_score']) for c in candidates),
            'suggested_action': 'Investigate'}
        proposal['id'] = signature({'kind': proposal['kind'], 'name': name,
            'accounts': sorted(c['crm_account_id'] for c in candidates)})
        proposals.append(proposal)

    # Orphans are candidates for investigation, not automatic deactivation.
    listed_ids = {m['crm_account_id'] for m in matches if float(m.get('match_score') or 0) >= 85}
    for account in accounts:
        if account.get('parent_id') != PARENT_ID or account['account_id'] in listed_ids:
            continue
        if 'parent account' in account.get('name', '').lower():
            continue
        proposal = {'kind': 'Bellhaven CRM-only account', 'facility': {}, 'account': account,
            'reason': 'Currently linked to Bellhaven but not confidently matched to a website facility; check for closure, rebrand, or sale.',
            'score': 0, 'suggested_action': 'Investigate'}
        proposal['id'] = signature({'kind': proposal['kind'], 'account': account['account_id']})
        proposals.append(proposal)
    return proposals


st.set_page_config(page_title='Bellhaven CRM Review', layout='wide')
st.title('Bellhaven | CRM Reconciliation Review')
st.caption('Website evidence → proposed action → reviewer approval → CRM API verification')

matches = rows('facility_crm_matches.csv')
duplicates = rows('potential_duplicate_accounts.csv')
facilities = rows('bellhaven_facilities.csv')
accounts = rows('crm_accounts.csv')
proposals = build_proposals(matches, duplicates, facilities, accounts)
db = connection()
decisions = decided(db)

write_enabled = os.getenv('CRM_WRITE_ENABLED', '0') == '1'
st.info('READ-ONLY MODE: API writes disabled.' if not write_enabled else
        'LIVE WRITE MODE: Approving a change can modify your CRM sandbox.')

c1, c2, c3, c4 = st.columns(4)
c1.metric('Website facilities', len(facilities))
c2.metric('CRM snapshot accounts', len(accounts))
c3.metric('Open proposals', sum(p['id'] not in decisions for p in proposals))
c4.metric('Decided proposals', sum(p['id'] in decisions for p in proposals))

with st.sidebar:
    st.header('Review settings')
    category = st.selectbox('Category', ['All'] + sorted({p['kind'] for p in proposals}))
    show_decided = st.checkbox('Show previously decided proposals', False)
    st.caption('Decision history is saved in review_decisions.sqlite3. Keep this file between runs.')
    if st.button('Test CRM connection (GET only)'):
        try:
            me = api_request('GET', '/me')
            st.success('CRM connection succeeded')
            st.json(me)
        except Exception as exc:
            st.error(str(exc))

visible = [p for p in proposals if (category == 'All' or p['kind'] == category)
           and (show_decided or p['id'] not in decisions)]
st.subheader(f'Review queue ({len(visible)})')
if not visible:
    st.success('No proposals in the current filter.')

for p in visible:
    f, a = p['facility'], p['account']
    label = f.get('name') or a.get('name') or 'Unknown account'
    with st.expander(f"{p['kind']} | {label} | score {p['score']}"):
        st.write('**Reason:**', p['reason'])
        left, right = st.columns(2)
        with left:
            st.markdown('**Website evidence**')
            st.json({k: f.get(k, '') for k in ('name','address','city','state','zip','care_offerings','source_url')})
        with right:
            st.markdown('**CRM snapshot (refresh live record before approving)**')
            st.json({k: a.get(k, '') for k in ('account_id','name','parent_id','parent_name','billing_street',
                'billing_city','billing_state','billing_zip','status','lifetime_revenue','outstanding_ar',
                'duplicate_of_account','chow_current_account','note')})
        if p.get('candidates'):
            st.markdown('**Duplicate candidates**')
            st.dataframe(p['candidates'], use_container_width=True)

        action = st.selectbox('Reviewer action',
            ['Investigate / no CRM write', 'Update existing account (PATCH)',
             'Create missing account (POST)', 'Mark duplicate inactive (PATCH)',
             'Change parent (CHOW-aware)'], key=f"action_{p['id']}")
        st.caption('Enter only the fields you intend to change. For a duplicate, set the losing account as the target.')
        target = st.text_input('Target CRM account ID (for PATCH / parent change)',
            value=a.get('account_id',''), key=f"target_{p['id']}")
        default_payload = {}
        if action == 'Create missing account (POST)':
            default_payload = {'name': f.get('name',''), 'parent_id': PARENT_ID,
                'billing_street': f.get('address',''), 'billing_city': f.get('city',''),
                'billing_state': f.get('state',''), 'billing_zip': f.get('zip',''),
                'status': 'Active'}
        elif action == 'Mark duplicate inactive (PATCH)':
            default_payload = {'status': 'Inactive', 'duplicate_of_account': '',
                'note': 'Duplicate of surviving CRM account; reviewed against website evidence.'}
        elif action == 'Change parent (CHOW-aware)':
            default_payload = {'parent_id': PARENT_ID,
                'name': f.get('name') or a.get('name', ''),
                'billing_street': f.get('address') or a.get('billing_street', ''),
                'billing_city': f.get('city') or a.get('billing_city', ''),
                'billing_state': f.get('state') or a.get('billing_state', ''),
                'billing_zip': f.get('zip') or a.get('billing_zip', ''),
                'status': 'Active'}
        payload_text = st.text_area('JSON change payload', json.dumps(default_payload, indent=2),
            key=f"payload_{p['id']}", height=150)
        note = st.text_input('Reviewer explanation (required)', key=f"note_{p['id']}")
        confirm = st.checkbox('I checked the evidence and the target account ID', key=f"confirm_{p['id']}")
        approve, reject = st.columns(2)
        with reject:
            if st.button('Reject proposal', key=f"reject_{p['id']}"):
                if not note.strip():
                    st.error('Enter a reviewer explanation before rejecting.')
                else:
                    db.execute('INSERT OR IGNORE INTO decisions (proposal_id,decision,action,payload,response) VALUES (?,?,?,?,?)',
                        (p['id'], 'Rejected', action, json.dumps({'note': note}), 'No API write'))
                    db.commit()
                    st.rerun()
        with approve:
            if st.button('Approve selected action', key=f"approve_{p['id']}", disabled=not write_enabled):
                if p['id'] in decisions:
                    st.warning('Already decided. No repeat action allowed.')
                    continue
                if not note.strip() or not confirm:
                    st.error('Enter a reason and check the confirmation box.')
                    continue
                try:
                    payload = json.loads(payload_text)
                    if not isinstance(payload, dict):
                        raise ValueError('Payload must be a JSON object')
                    if action == 'Investigate / no CRM write':
                        raise ValueError('Use Reject for no-write decisions; choose a concrete action to approve.')
                    if action in ('Update existing account (PATCH)', 'Mark duplicate inactive (PATCH)', 'Change parent (CHOW-aware)') and not target:
                        raise ValueError('Target account ID required')
                    if action == 'Update existing account (PATCH)' and ('chow_current_account' in payload or 'parent_id' in payload):
                        raise ValueError('Use Change parent (CHOW-aware) for ownership updates and CHOW links')
                    if action == 'Mark duplicate inactive (PATCH)':
                        if 'parent_id' in payload or 'chow_current_account' in payload:
                            raise ValueError('Duplicate action cannot change parent or CHOW link')
                        if payload.get('status') != 'Inactive' or not payload.get('duplicate_of_account'):
                            raise ValueError('Provide status Inactive and a surviving account ID in duplicate_of_account')
                        if target == payload['duplicate_of_account']:
                            raise ValueError('Duplicate cannot point to itself')
                    if action == 'Change parent (CHOW-aware)':
                        if not payload.get('parent_id'):
                            raise ValueError('Destination parent_id required')
                        live = live_account(target)
                        if not requires_chow(live) and set(payload) != {'parent_id'}:
                            raise ValueError('Direct reparent requires only parent_id in the JSON payload')
                        # Verify destination parent account exists before any write.
                        live_account(str(payload['parent_id']))
                        if requires_chow(live):
                            st.info('Protected CHOW: creating a new account and linking the old one.')
                        else:
                            st.info('Direct reparent allowed: revenue or outstanding AR is zero.')
                    # Recheck target live record before any PATCH.
                    if action in ('Update existing account (PATCH)', 'Mark duplicate inactive (PATCH)'):
                        live = extract_account(api_request('GET', f'/accounts/{target}'))
                        if live.get('account_id') != target:
                            raise ValueError('Live CRM account identity could not be confirmed')
                        if 'parent_id' in payload and payload['parent_id'] != live.get('parent_id') and money(live.get('lifetime_revenue')) > 0 and money(live.get('outstanding_ar')) > 0:
                            raise ValueError('CHOW protection: parent update blocked for account with revenue and AR')
                    if action == 'Create missing account (POST)':
                        if not payload.get('name') or not payload.get('parent_id'):
                            raise ValueError('New account needs name and parent_id')
                        # Search fresh CRM for exact name and address to reduce accidental duplicates.
                        if matching_live_accounts(payload):
                            raise ValueError('Matching live account exists; creation blocked')
                    if action == 'Create missing account (POST)':
                        response = api_request('POST', '/accounts', payload)
                    elif action == 'Change parent (CHOW-aware)':
                        response = chow_transfer(db, target, payload['parent_id'],
                                                 payload if requires_chow(live) else {})
                    else:
                        response = api_request('PATCH', f'/accounts/{target}', payload)
                    # API success is recorded; refresh live account to verify in CRM browser.
                    db.execute('INSERT OR IGNORE INTO decisions (proposal_id,decision,action,payload,response) VALUES (?,?,?,?,?)',
                        (p['id'], 'Approved', action, json.dumps({'target': target, 'change': payload, 'reviewer_note': note}), json.dumps(response)))
                    db.commit()
                    st.success('API returned success. Verify the change in the CRM browser.')
                    st.rerun()
                except Exception as exc:
                    st.error(f'No confirmed approval: {exc}')

st.divider()
st.subheader('Decision history')
history = db.execute('SELECT proposal_id,decision,action,payload,decided_at FROM decisions ORDER BY decided_at DESC').fetchall()
if history:
    st.dataframe([{'proposal_id': r[0], 'decision': r[1], 'action': r[2],
                   'payload': r[3], 'decided_at': r[4]} for r in history], use_container_width=True)
else:
    st.caption('No decisions recorded yet.')
st.subheader('CHOW recovery — only for interrupted, previously approved transfers')
st.caption('Use only if a CHOW POST may have succeeded but the application could not record or link the new account. Verify the new ID in the CRM browser first. No new account is created here.')
pending = db.execute("SELECT old_account_id,new_account_id,status FROM chow_operations WHERE status!='COMPLETE'").fetchall()
if pending:
    st.dataframe([{'historical_account': r[0], 'new_account': r[1] or 'UNKNOWN',
                   'status': r[2]} for r in pending], use_container_width=True)
    old_recovery = st.text_input('Historical account ID for recovery')
    new_recovery = st.text_input('Verified new CRM account ID')
    recovery_confirm = st.checkbox('I verified both accounts in the live CRM and approve linking them')
    if st.button('Recover CHOW link', disabled=not write_enabled):
        if not old_recovery.strip() or not new_recovery.strip() or not recovery_confirm:
            st.error('Enter both verified account IDs and confirm before proceeding')
        else:
            try:
                result = recover_chow(db, old_recovery.strip(), new_recovery.strip())
                st.success('CHOW link verified in live CRM')
                st.json(result)
            except Exception as exc:
                st.error(f'CHOW recovery not confirmed: {exc}')
else:
    st.caption('No interrupted CHOW operations recorded.')

st.warning('CHOW transfers must be based on verified ownership evidence. Historical records are preserved except for chow_current_account. The app will not repeat an uncertain POST automatically.')
