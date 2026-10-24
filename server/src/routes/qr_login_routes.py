import hashlib
from flask import Blueprint, jsonify, request
from src.utils.auth_utils import token_required
from src.services.qr_login_service import start_login, get_status

qr_login_bp = Blueprint('qr_login', __name__)


def owner():
    return hashlib.sha256(request.cookies.get('token', '').encode()).hexdigest()


@qr_login_bp.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@qr_login_bp.route('/admin/api/qr-login/<cloud>', methods=['POST'])
@token_required
def start(cloud):
    try:
        data = request.get_json(silent=True) or {}
        return jsonify(success=True, **start_login(cloud, owner(), data.get('account_id'), data.get('confirm_legacy') is True))
    except Exception:
        return jsonify(success=False, message='获取二维码失败，请稍后重试'), 502


@qr_login_bp.route('/admin/api/qr-login/session/<sid>', methods=['GET', 'DELETE'])
@token_required
def status(sid):
    result = get_status(sid, owner(), cancel=request.method == 'DELETE')
    if result is None:
        return jsonify(success=False, message='二维码已过期，请重新获取'), 404
    return jsonify(success=True, **result)
