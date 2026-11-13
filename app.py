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


def signature(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]


def connection():
    db = sqlite3.connect(DB)
    db.execute('''CREATE TABLE IF NOT EXISTS decisions (
      proposal_id TEXT PRIMARY KEY, decision TEXT NOT NULL,
      action TEXT NOT NULL, payload TEXT NOT NULL,
      response TEXT, decided_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
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
            default_payload = {'parent_id': PARENT_ID}
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
                    if action == 'Mark duplicate inactive (PATCH)':
                        if payload.get('status') != 'Inactive' or not payload.get('duplicate_of_account'):
                            raise ValueError('Provide status Inactive and a surviving account ID in duplicate_of_account')
                        if target == payload['duplicate_of_account']:
                            raise ValueError('Duplicate cannot point to itself')
                    if action == 'Change parent (CHOW-aware)':
                        live = extract_account(api_request('GET', f'/accounts/{target}'))
                        if not live.get('account_id'):
                            raise ValueError('Live CRM account response lacks account_id; verify response shape')
                        if money(live.get('lifetime_revenue')) > 0 and money(live.get('outstanding_ar')) > 0:
                            raise ValueError('CHOW PROTECTED: do not re-parent this account. Create a new account first, then separately link chow_current_account on the old account. No write was made.')
                        if set(payload) != {'parent_id'}:
                            raise ValueError('Parent change must contain only parent_id')
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
                        lookup = api_request('GET', '/accounts?q=' + requests.utils.quote(payload['name']))
                        candidates = lookup.get('accounts', lookup.get('items', lookup.get('data', []))) if isinstance(lookup, dict) else lookup
                        if not isinstance(candidates, list):
                            raise ValueError('Could not interpret live CRM search results; creation blocked')
                        for existing in candidates:
                            if normalize(existing.get('name')) == normalize(payload['name']) and normalize(existing.get('billing_street')) == normalize(payload.get('billing_street')):
                                raise ValueError('Matching live account exists; creation blocked')
                    if action == 'Create missing account (POST)':
                        response = api_request('POST', '/accounts', payload)
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
st.warning('Important: CHOW-protected transfers require a separate approved create-and-link workflow. This app blocks direct re-parenting but does not yet automate both CHOW steps. Do not use a generic PATCH to bypass that rule.')
