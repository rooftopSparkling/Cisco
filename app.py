"""
디지털 미디어 공유 플랫폼 – Flask 백엔드
EC2 (Private Subnet) 에서 실행, ALB → EC2 → S3/RDS/CloudFront 연동
"""

import uuid
import boto3
import psutil
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, flash)
from botocore.exceptions import ClientError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload

from config import Config
from models import db, Media, MediaFile, User

# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

s3 = boto3.client('s3', region_name=Config.AWS_REGION)

# ── Read Replica 세션 ──────────────────────────────────────────────────────────
_replica_engine = create_engine(
    Config.DB_REPLICA_URI,
    connect_args={'ssl': {'ca': Config.SSL_CA}},
    pool_pre_ping=True,
)
ReplicaSession = sessionmaker(bind=_replica_engine)

def get_replica():
    """읽기 전용 DB 세션 반환"""
    return ReplicaSession()

MULTIPART_THRESHOLD = Config.SIMPLE_UPLOAD_LIMIT_MB * 1024 * 1024


class Pagination:
    """replica 세션용 간단한 페이지네이션 래퍼"""
    def __init__(self, items, total, page, per_page):
        self.items    = items
        self.total    = total
        self.page     = page
        self.per_page = per_page
        self.pages    = max(1, (total + per_page - 1) // per_page)
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1 if self.has_prev else None
        self.next_num = page + 1 if self.has_next else None

    def iter_pages(self, left_edge=2, right_edge=2, left_current=2, right_current=3):
        last = 0
        for num in range(1, self.pages + 1):
            if (num <= left_edge
                    or self.page - left_current - 1 < num < self.page + right_current
                    or num > self.pages - right_edge):
                if last + 1 != num:
                    yield None
                yield num
                last = num

ALLOWED_TYPES = {
    'video/mp4', 'video/webm', 'video/ogg', 'video/quicktime',
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
}


# ── 인증 헬퍼 ──────────────────────────────────────────────────────────────────

def current_user():
    uid = session.get('user_id')
    if uid:
        return User.query.get(uid)
    return None

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            flash('로그인이 필요합니다.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

@app.context_processor
def inject_user():
    return {'current_user': current_user()}


@app.template_filter('humanize')
def humanize_size(size):
    if not size:
        return '–'
    size = float(size)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


# ── 페이지 라우트 ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    page     = request.args.get('page', 1, type=int)
    query    = request.args.get('q', '').strip()
    per_page = 12

    replica = get_replica()
    try:
        base_q = replica.query(Media).filter_by(is_public=True)
        if query:
            base_q = base_q.filter(Media.title.contains(query))
        total = base_q.count()
        items = (base_q.options(joinedload(Media.files))
                       .order_by(Media.created_at.desc())
                       .offset((page - 1) * per_page)
                       .limit(per_page).all())
    finally:
        replica.close()

    pagination = Pagination(items, total, page, per_page)
    return render_template('index.html', pagination=pagination, query=query)


@app.route('/upload')
@login_required
def upload():
    return render_template('upload.html',
                           limit_mb=Config.SIMPLE_UPLOAD_LIMIT_MB)


@app.route('/post/<int:media_id>')
def media_detail(media_id):
    # 조회수 업데이트는 master
    media = Media.query.get_or_404(media_id)
    media.views += 1
    db.session.commit()

    # 연관 게시물은 replica
    replica = get_replica()
    try:
        related = (replica.query(Media)
                   .options(joinedload(Media.files))
                   .filter(Media.id != media_id, Media.is_public == True)
                   .order_by(Media.created_at.desc())
                   .limit(6).all())
    finally:
        replica.close()

    user = current_user()
    can_delete = user and (user.is_admin or media.user_id == user.id)
    return render_template('media_detail.html', media=media,
                           related=related, can_delete=can_delete)


# ── 인증 라우트 ────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user():
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username, is_active=True).first()
        if user and user.check_password(password):
            session.permanent = True
            session['user_id'] = user.id
            user.last_login = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('index'))
        flash('아이디 또는 비밀번호가 올바르지 않습니다.')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user():
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        email    = request.form.get('email', '').strip() or None

        if not username or not password:
            flash('아이디와 비밀번호를 입력하세요.')
        elif len(username) < 3:
            flash('아이디는 3자 이상이어야 합니다.')
        elif len(password) < 6:
            flash('비밀번호는 6자 이상이어야 합니다.')
        elif User.query.filter_by(username=username).first():
            flash('이미 사용 중인 아이디입니다.')
        else:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            session.permanent = True
            session['user_id'] = user.id
            return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# ── 관리자 대시보드 ────────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin_dashboard():
    # 기본 통계
    total_media  = Media.query.count()
    total_users  = User.query.count()
    total_views  = db.session.query(db.func.sum(Media.views)).scalar() or 0
    total_size   = db.session.query(db.func.sum(Media.file_size)).scalar() or 0
    video_count  = Media.query.filter(Media.file_type.like('video/%')).count()
    image_count  = Media.query.filter(Media.file_type.like('image/%')).count()

    # EC2 컴퓨팅 (psutil)
    cpu_percent  = psutil.cpu_percent(interval=0.5)
    mem          = psutil.virtual_memory()
    disk         = psutil.disk_usage('/')

    # 업로드 기록 (최근 50건)
    recent_uploads = Media.query.order_by(Media.created_at.desc()).limit(50).all()

    # S3 오브젝트 목록
    s3_objects, s3_error = [], None
    try:
        resp = s3.list_objects_v2(Bucket=Config.S3_BUCKET, MaxKeys=200)
        s3_objects = resp.get('Contents', [])
    except ClientError as e:
        s3_error = str(e)

    # RDS 미디어 메타데이터 (최근 100건)
    media_list = Media.query.order_by(Media.id.desc()).limit(100).all()

    return render_template('admin.html',
                           total_media=total_media,
                           total_users=total_users,
                           total_views=total_views,
                           total_size=total_size,
                           video_count=video_count,
                           image_count=image_count,
                           cpu_percent=cpu_percent,
                           mem_used=mem.used,
                           mem_total=mem.total,
                           mem_percent=mem.percent,
                           disk_used=disk.used,
                           disk_total=disk.total,
                           disk_percent=disk.percent,
                           recent_uploads=recent_uploads,
                           s3_objects=s3_objects,
                           s3_error=s3_error,
                           media_list=media_list)


@app.route('/admin/users')
@admin_required
def admin_users():
    users = User.query.order_by(User.id.asc()).all()
    return render_template('admin_users.html', users=users)


@app.route('/admin/users/<int:user_id>/role', methods=['POST'])
@admin_required
def admin_change_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.username == 'admin':
        return redirect(url_for('admin_users'))
    new_role = request.form.get('role')
    if new_role in ('admin', 'user'):
        user.role = new_role
        db.session.commit()
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('admin_users'))


# ── API: 단순 업로드 (≤ SIMPLE_UPLOAD_LIMIT_MB) ────────────────────────────────

@app.route('/api/presign', methods=['POST'])
@login_required
def api_presign():
    data      = request.get_json(silent=True) or {}
    filename  = data.get('filename', '')
    file_type = data.get('fileType', '')
    file_size = int(data.get('fileSize', 0))

    if file_type not in ALLOWED_TYPES:
        return jsonify({'error': '허용되지 않는 파일 형식입니다.'}), 400
    if file_size > MULTIPART_THRESHOLD:
        return jsonify({'error': f'{Config.SIMPLE_UPLOAD_LIMIT_MB}MB 초과 파일은 멀티파트 업로드를 사용하세요.'}), 400

    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    folder = 'image' if file_type.startswith('image/') else 'media'
    key = f"{folder}/{uuid.uuid4()}.{ext}"

    try:
        presigned = s3.generate_presigned_post(
            Bucket=Config.S3_BUCKET,
            Key=key,
            Fields={'Content-Type': file_type},
            Conditions=[
                {'Content-Type': file_type},
                ['content-length-range', 1, MULTIPART_THRESHOLD],
            ],
            ExpiresIn=3600,
        )
    except ClientError as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'presigned': presigned, 'key': key})


# ── API: Multipart 업로드 ───────────────────────────────────────────────────────

@app.route('/api/multipart/init', methods=['POST'])
@login_required
def api_multipart_init():
    data      = request.get_json(silent=True) or {}
    filename  = data.get('filename', '')
    file_type = data.get('fileType', '')

    if file_type not in ALLOWED_TYPES:
        return jsonify({'error': '허용되지 않는 파일 형식입니다.'}), 400

    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    folder = 'image' if file_type.startswith('image/') else 'media'
    key = f"{folder}/{uuid.uuid4()}.{ext}"

    try:
        resp = s3.create_multipart_upload(
            Bucket=Config.S3_BUCKET,
            Key=key,
            ContentType=file_type,
        )
    except ClientError as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'uploadId': resp['UploadId'], 'key': key})


@app.route('/api/multipart/presign-part', methods=['POST'])
@login_required
def api_multipart_presign_part():
    data        = request.get_json(silent=True) or {}
    key         = data.get('key')
    upload_id   = data.get('uploadId')
    part_number = int(data.get('partNumber', 1))

    try:
        url = s3.generate_presigned_url(
            'upload_part',
            Params={
                'Bucket':     Config.S3_BUCKET,
                'Key':        key,
                'UploadId':   upload_id,
                'PartNumber': part_number,
            },
            ExpiresIn=3600,
        )
    except ClientError as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'url': url})


@app.route('/api/multipart/complete', methods=['POST'])
@login_required
def api_multipart_complete():
    data      = request.get_json(silent=True) or {}
    key       = data.get('key')
    upload_id = data.get('uploadId')
    parts     = data.get('parts', [])

    try:
        s3.complete_multipart_upload(
            Bucket=Config.S3_BUCKET,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts},
        )
    except ClientError as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'success': True, 'key': key})


@app.route('/api/multipart/abort', methods=['POST'])
@login_required
def api_multipart_abort():
    data      = request.get_json(silent=True) or {}
    key       = data.get('key')
    upload_id = data.get('uploadId')
    try:
        s3.abort_multipart_upload(
            Bucket=Config.S3_BUCKET, Key=key, UploadId=upload_id
        )
    except ClientError:
        pass
    return jsonify({'success': True})


# ── API: 미디어 저장 ───────────────────────────────────────────────────────────

@app.route('/api/media', methods=['POST'])
@login_required
def api_save_media():
    data        = request.get_json(silent=True) or {}
    files       = data.get('files', [])
    title       = data.get('title', 'Untitled')
    description = data.get('description', '')

    if not files:
        return jsonify({'error': 'files is required'}), 400

    user = current_user()
    media = _save_media_files(title, description, files, user.id)
    return jsonify({'success': True, 'mediaId': media.id,
                    'url': media.primary_url})


# ── API: 미디어 삭제 ──────────────────────────────────────────────────────────

@app.route('/api/media/<int:media_id>', methods=['DELETE'])
@login_required
def api_delete_media(media_id):
    media = Media.query.get_or_404(media_id)
    user = current_user()

    if not user.is_admin and media.user_id != user.id:
        return jsonify({'error': '권한이 없습니다.'}), 403

    keys_to_delete = [mf.s3_key for mf in media.files]
    if not keys_to_delete and media.s3_key:
        keys_to_delete = [media.s3_key]
    for key in keys_to_delete:
        try:
            s3.delete_object(Bucket=Config.S3_BUCKET, Key=key)
        except ClientError:
            pass
    db.session.delete(media)
    db.session.commit()
    return jsonify({'success': True})


# ── API: 미디어 목록 ───────────────────────────────────────────────────────────

@app.route('/api/media', methods=['GET'])
def api_list_media():
    page  = request.args.get('page', 1, type=int)
    items = (Media.query
             .filter_by(is_public=True)
             .order_by(Media.created_at.desc())
             .paginate(page=page, per_page=12, error_out=False))
    return jsonify({
        'items': [m.to_dict() for m in items.items],
        'total': items.total,
        'pages': items.pages,
        'page':  items.page,
    })


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _save_media_files(title, description, files, user_id=None):
    first = files[0]
    total_size = sum(int(f.get('fileSize', 0)) for f in files)

    media = Media(
        title=title,
        description=description,
        s3_key=first['key'],
        cloudfront_url=f"https://{Config.CLOUDFRONT_DOMAIN}/{first['key']}",
        file_type=first.get('fileType', ''),
        file_size=total_size,
        user_id=user_id,
    )
    db.session.add(media)
    db.session.flush()

    for i, f in enumerate(files):
        mf = MediaFile(
            media_id=media.id,
            s3_key=f['key'],
            cloudfront_url=f"https://{Config.CLOUDFRONT_DOMAIN}/{f['key']}",
            file_type=f.get('fileType', ''),
            file_size=int(f.get('fileSize', 0)),
            order=i,
        )
        db.session.add(mf)

    db.session.commit()
    return media


# ── 진입점 ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=False)
