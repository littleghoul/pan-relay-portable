"""Refresh credentials by verified provider identity, retaining account IDs."""
import json
from src.db.connection import get_db_connection


def extra(value):
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def save_login(cloud, credential, identity, legacy=None, target_id=None, confirm_legacy=False):
    if not isinstance(identity, str) or not identity:
        raise ValueError('无法确认网盘账号身份')
    conn = get_db_connection()
    if not conn:
        raise ValueError('账号数据库不可用')
    cursor = conn.cursor(as_dict=True)
    try:
        cursor.execute('BEGIN IMMEDIATE')
        cursor.execute('SELECT * FROM cloud_accounts WHERE cloud_name=? ORDER BY id', (cloud,))
        rows = cursor.fetchall()
        if target_id is not None:
            selected = next((r for r in rows if r['id'] == target_id), None)
            if not selected:
                raise ValueError('原账号不存在')
            stored = extra(selected.get('extra_data')).get('provider_user_id')
            proof = (legacy or {}).get(target_id)
            expected = stored or (proof or {}).get('identity')
            if expected and expected != identity:
                raise ValueError('扫码账号与原账号不一致')
            if not expected and not confirm_legacy:
                raise ValueError('请确认原账号身份后重新登录')
        matches = []
        for row in rows:
            data = extra(row.get('extra_data'))
            stored = data.get('provider_user_id')
            proof = (legacy or {}).get(row['id'])
            if stored == identity or (not stored and proof and proof['identity'] == identity and proof['credential'] == row['credential']) or row['id'] == target_id:
                data['provider_user_id'] = identity
                cursor.execute('UPDATE cloud_accounts SET credential=?,extra_data=?,is_valid=1,invalid_reason=NULL WHERE id=?',
                               (credential, json.dumps(data, ensure_ascii=False), row['id']))
                matches.append(row['id'])
        if matches:
            conn.commit()
            return matches[0], True
        cursor.execute('INSERT INTO cloud_accounts(cloud_name,account_name,credential,extra_data,is_active,is_valid) VALUES(?,?,?,?,1,1)',
                       (cloud, cloud + '-扫码登录', credential, json.dumps({'provider_user_id': identity})))
        account_id = cursor.lastrowid
        conn.commit()
        return account_id, False
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
