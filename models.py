from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ── 사용자 ─────────────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer,      primary_key=True)
    username      = db.Column(db.String(50),   unique=True, nullable=False)
    password_hash = db.Column(db.String(255),  nullable=False)
    email         = db.Column(db.String(255),  unique=True, nullable=True)
    role          = db.Column(db.Enum('admin', 'user'), default='user', nullable=False)
    is_active     = db.Column(db.Boolean,      default=True)
    created_at    = db.Column(db.DateTime,     default=datetime.utcnow)
    last_login    = db.Column(db.DateTime,     nullable=True)

    media = db.relationship('Media', backref='uploader', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'


# ── 미디어 파일 (게시물당 N개) ──────────────────────────────────────────────────

class MediaFile(db.Model):
    __tablename__ = 'media_files'

    id             = db.Column(db.Integer,      primary_key=True)
    media_id       = db.Column(db.Integer,      db.ForeignKey('media.id'), nullable=False)
    s3_key         = db.Column(db.String(500),  nullable=False)
    cloudfront_url = db.Column(db.String(1000), nullable=False)
    file_type      = db.Column(db.String(100),  nullable=True)
    file_size      = db.Column(db.BigInteger,   nullable=True)
    order          = db.Column(db.Integer,      default=0)

    @property
    def is_video(self):
        return self.file_type and self.file_type.startswith('video/')

    @property
    def is_image(self):
        return self.file_type and self.file_type.startswith('image/')

    @property
    def size_human(self):
        if not self.file_size:
            return '–'
        size = float(self.file_size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


# ── 미디어 게시물 ───────────────────────────────────────────────────────────────

class Media(db.Model):
    __tablename__ = 'media'

    id              = db.Column(db.Integer,     primary_key=True)
    title           = db.Column(db.String(255), nullable=False)
    description     = db.Column(db.Text,        nullable=True)
    s3_key          = db.Column(db.String(500), nullable=True)
    cloudfront_url  = db.Column(db.String(1000), nullable=True)
    thumbnail_url   = db.Column(db.String(1000), nullable=True)
    file_type       = db.Column(db.String(100), nullable=True)
    file_size       = db.Column(db.BigInteger,  nullable=True)
    is_public       = db.Column(db.Boolean,     default=True)
    views           = db.Column(db.Integer,     default=0)
    created_at      = db.Column(db.DateTime,    default=datetime.utcnow)
    user_id         = db.Column(db.Integer,     db.ForeignKey('users.id'), nullable=True)

    files = db.relationship('MediaFile', backref='media', lazy=True,
                            order_by='MediaFile.order', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id':             self.id,
            'title':          self.title,
            'description':    self.description,
            'cloudfront_url': self.primary_url,
            'thumbnail_url':  self.thumbnail_url,
            'file_type':      self.primary_file_type,
            'file_size':      self.file_size,
            'is_public':      self.is_public,
            'views':          self.views,
            'created_at':     self.created_at.isoformat(),
            'uploader':       self.uploader.username if self.uploader else '익명',
        }

    @property
    def primary_url(self):
        if self.files:
            return self.files[0].cloudfront_url
        return self.cloudfront_url

    @property
    def primary_file_type(self):
        if self.files:
            return self.files[0].file_type
        return self.file_type

    @property
    def is_video(self):
        ft = self.primary_file_type
        return ft and ft.startswith('video/')

    @property
    def is_image(self):
        ft = self.primary_file_type
        return ft and ft.startswith('image/')

    @property
    def size_human(self):
        if not self.file_size:
            return '–'
        size = float(self.file_size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
