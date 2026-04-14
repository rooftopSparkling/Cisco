import os
from urllib.parse import quote_plus

class Config:
    # ── AWS 설정 ───────────────────────────────────────────────────────
    AWS_REGION        = 'ap-northeast-2'
    S3_BUCKET         = 'your-s3-bucket-name'
    CLOUDFRONT_DOMAIN = 'your-cloudfront-domain.cloudfront.net'

    # ── RDS 설정 ───────────────────────────────────────────────────────
    DB_HOST     = 'your-rds-endpoint.rds.amazonaws.com'
    DB_PORT     = '3306'
    DB_NAME     = 'your_db_name'
    DB_USER     = 'your_db_user'
    DB_PASSWORD = 'your_db_password'

    SSL_CA = '/home/ubuntu/app/global-bundle.pem'

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASSWORD)}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {
        'connect_args': {
            'ssl': {'ca': SSL_CA}
        }
    }
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── Flask ──────────────────────────────────────────────────────────
    SECRET_KEY             = 'change-me-to-a-random-secret-key'
    SIMPLE_UPLOAD_LIMIT_MB = 10

    # ── 세션 ───────────────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400  # 24시간
