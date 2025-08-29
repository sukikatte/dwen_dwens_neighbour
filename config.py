import os

class Config:
    """基础配置类"""
    SECRET_KEY = 'animal_protection_platform_secret_key_2025'
    PERMANENT_SESSION_LIFETIME = 7  # 天
    
    # 会话配置
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_TYPE = 'filesystem'
    SESSION_FILE_THRESHOLD = 500

class GitLabConfig(Config):
    """GitLab环境配置 - 使用原始Firebase项目"""
    FIREBASE_JSON_PATH = 'animalprotection-ba7f1-firebase-adminsdk-fbsvc-170185b5d3.json'
    FIREBASE_DATABASE_URL = 'https://animalprotection-ba7f1-default-rtdb.asia-southeast1.firebasedatabase.app'
    FIREBASE_STORAGE_BUCKET = 'animalprotection-ba7f1.appspot.com'
    FIREBASE_PROJECT_ID = 'animalprotection-ba7f1'

class GitHubConfig(Config):
    """GitHub环境配置 - 使用新的Firebase项目"""
    # 这些值需要在GitHub上创建新的Firebase项目后更新
    FIREBASE_JSON_PATH = 'new-firebase-adminsdk.json'  # 新的Firebase配置文件
    FIREBASE_DATABASE_URL = 'https://your-new-project-default-rtdb.asia-southeast1.firebasedatabase.app'
    FIREBASE_STORAGE_BUCKET = 'your-new-project.appspot.com'
    FIREBASE_PROJECT_ID = 'your-new-project'

# 根据环境变量选择配置
def get_config():
    """根据环境变量返回相应的配置"""
    environment = os.getenv('FLASK_ENV', 'gitlab').lower()
    
    if environment == 'github':
        return GitHubConfig()
    else:
        return GitLabConfig()

# 获取当前配置
config = get_config() 