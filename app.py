import random
from flask import Flask, render_template, redirect, url_for, request, session, jsonify
from firebase_admin import credentials, initialize_app, auth, db
import os
from werkzeug.utils import secure_filename
import requests
from bs4 import BeautifulSoup
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import uuid
import time
import math
import json

from flask_session import Session
from config import config

print(os.urandom(24))

app = Flask(__name__)

# 设置调度器
scheduler = BackgroundScheduler()

# 使用配置类设置应用
app.secret_key = config.SECRET_KEY
app.permanent_session_lifetime = timedelta(days=config.PERMANENT_SESSION_LIFETIME)

# 会话配置
app.config.update(
    SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=config.SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=config.SESSION_COOKIE_SAMESITE,
    SESSION_TYPE=config.SESSION_TYPE,
    SESSION_FILE_DIR=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flask_session'),
    SESSION_FILE_THRESHOLD=config.SESSION_FILE_THRESHOLD
)

# 初始化会话
Session(app)

# Firebase Admin SDK initialization
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(BASE_DIR, config.FIREBASE_JSON_PATH)

cred = credentials.Certificate(json_path)

initialize_app(cred, {
    'databaseURL': config.FIREBASE_DATABASE_URL,
    'storageBucket': config.FIREBASE_STORAGE_BUCKET,
    'projectId': config.FIREBASE_PROJECT_ID
})

# 添加 CORS 支持
CORS(app, resources={r"/*": {"origins": "*"}})






# 会话检查中间件
@app.before_request
def check_session():
    # 排除不需要登录的路由
    if request.endpoint in ['static', 'login', 'register', 'index']:
        return None

    # 检查用户是否已登录
    if 'user' not in session:
        app.logger.warning(f"Session check failed for {request.path}. Session: {session}")
        # 如果是 AJAX 请求，返回 JSON 响应
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': 'Session expired', 'redirect': url_for('login')}), 401
        return redirect(url_for('login'))

    # 确保会话是永久的
    session.permanent = True
    return None






@app.route('/set_session', methods=['POST'])
def set_session():
    try:
        data = request.get_json()
        if not data or 'token' not in data:
            return jsonify({'status': 'error', 'message': 'No token provided'}), 400

        # 验证 Firebase ID token
        decoded_token = auth.verify_id_token(data['token'])
        uid = decoded_token['uid']
        
        # 获取用户信息
        user = auth.get_user(uid)
        
        # 设置会话
        session['user'] = {
            'uid': user.uid,
            'email': user.email,
            'display_name': user.display_name or user.email.split('@')[0]
        }
        session.permanent = True
        
        return jsonify({
            'status': 'success',
            'message': 'Session set successfully'
        })
    except Exception as e:
        app.logger.error(f"Error setting session: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 401


@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('knowledge'))
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            data = request.get_json()
            email = data.get('email')
            password = data.get('password')

            if not email or not password:
                return jsonify({
                    "status": "error",
                    "message": "Email and password are required"
                }), 400

            user = auth.get_user_by_email(email)

            # 存储用户信息到会话
            session['user'] = {
                'uid': user.uid,
                'email': user.email,
                'display_name': user.display_name or email
            }
            session.permanent = True
            session.modified = True

            return jsonify({
                "status": "success",
                "message": "Login successful",
                "redirect_url": url_for('knowledge')
            }), 200

        except Exception as e:
            app.logger.error(f"Login error: {str(e)}")
            return jsonify({
                "status": "error",
                "message": f"Login failed: {str(e)}"
            }), 500

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        data = request.get_json()
        name = data.get('name')
        email = data.get('email')
        password = data.get('password')

        try:
            # Create Firebase Authentication User
            user = auth.create_user(email=email, password=password, display_name=name)

            # Use email prefix as the key for consistency
            email_prefix = email.split('@')[0]

            # Store additional user data in Firebase Realtime Database
            db.reference(f'users/{email_prefix}').set({
                'name': name,
                'email': email,
                'uid': user.uid,
                'avatar': '/static/images/default-avatar.jpeg',
                'score': 0,
                'pets': []
            })

            return jsonify({"status": "success", "redirect_url": url_for('login')}), 200

        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    return render_template('register.html')


@app.route('/profile')
def profile():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]  # Use email prefix as keyId

    # Get user data from Firebase
    user_data = db.reference(f"users/{email_prefix}").get()

    # 如果user_data为None，初始化为空字典，防止后续出错
    if not user_data:
        user_data = {}

    return render_template('profile.html', user_info=user_info, user_data=user_data)


@app.route('/update_username', methods=['POST'])
def update_username():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    new_username = data.get("username")
    email_prefix = session['user']['email'].split('@')[0]  # 获取 keyId

    try:
        # 更新 Firebase Authentication 的 display_name
        auth.update_user(session['user']['uid'], display_name=new_username)

        # 更新 Firebase Realtime Database 的用户名
        db.reference(f'users/{email_prefix}').update({"name": new_username})

        # 同步更新 session，避免刷新页面后仍然显示旧用户名
        session['user']['display_name'] = new_username

        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/update_password', methods=['POST'])
def update_password():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    new_password = data.get("password")
    uid = session['user']['uid']

    try:
        # 更新 Firebase Authentication 密码
        auth.update_user(uid, password=new_password)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/update_avatar', methods=['POST'])
def update_avatar():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    new_avatar_url = data.get("avatar")  # New avatar URL passed from the frontend
    email_prefix = session['user']['email'].split('@')[0]  # Extract email prefix as the key

    # 去除URL中的主机地址部分，只保留相对路径
    if new_avatar_url and ('http://' in new_avatar_url or 'https://' in new_avatar_url):
        # 使用正则表达式提取出/static/images/部分
        import re
        match = re.search(r'(/static/images/.*)', new_avatar_url)
        if match:
            new_avatar_url = match.group(1)
    
    try:
        # Update the avatar in the Firebase Realtime Database
        db.reference(f'users/{email_prefix}').update({"avatar": new_avatar_url})

        # Also update the session avatar (for immediate update on the page without refreshing)
        session['user']['avatar'] = new_avatar_url

        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/knowledge')
def knowledge():
    # app.logger.debug("Session: %s", session)
    # if 'user' not in session:
    #  return redirect(url_for('login'))
    # return render_template('knowledge.html')

    # 添加省份地图数据
    province_data = {
        '上海': url_for('static', filename='json/data/data-1482909900836-H1BC_1WHg.json'),
        '河北': url_for('static', filename='json/data/data-1482909799572-Hkgu_yWSg.json'),
        '山西': url_for('static', filename='json/data/data-1482909909703-SyCA_JbSg.json'),
        '内蒙古': url_for('static', filename='json/data/data-1482909841923-rkqqdyZSe.json'),
        '辽宁': url_for('static', filename='json/data/data-1482909836074-rJV9O1-Hg.json'),
        '吉林': url_for('static', filename='json/data/data-1482909832739-rJ-cdy-Hx.json'),
        '黑龙江': url_for('static', filename='json/data/data-1482909803892-Hy4__J-Sx.json'),
        '江苏': url_for('static', filename='json/data/data-1482909823260-HkDtOJZBx.json'),
        '浙江': url_for('static', filename='json/data/data-1482909960637-rkZMYkZBx.json'),
        '安徽': url_for('static', filename='json/data/data-1482909768458-HJlU_yWBe.json'),
        '福建': url_for('static', filename='json/data/data-1478782908884-B1H6yezWe.json'),
        '江西': url_for('static', filename='json/data/data-1482909827542-r12YOJWHe.json'),
        '山东': url_for('static', filename='json/data/data-1482909892121-BJ3auk-Se.json'),
        '河南': url_for('static', filename='json/data/data-1482909807135-SJPudkWre.json'),
        '湖北': url_for('static', filename='json/data/data-1482909813213-Hy6u_kbrl.json'),
        '湖南': url_for('static', filename='json/data/data-1482909818685-H17FOkZSl.json'),
        '广东': url_for('static', filename='json/data/data-1482909784051-BJgwuy-Sl.json'),
        '广西': url_for('static', filename='json/data/data-1482909787648-SyEPuJbSg.json'),
        '海南': url_for('static', filename='json/data/data-1482909796480-H12P_J-Bg.json'),
        '四川': url_for('static', filename='json/data/data-1482909931094-H17eKk-rg.json'),
        '贵州': url_for('static', filename='json/data/data-1482909791334-Bkwvd1bBe.json'),
        '云南': url_for('static', filename='json/data/data-1482909957601-HkA-FyWSx.json'),
        '西藏': url_for('static', filename='json/data/data-1482927407942-SkOV6Qbrl.json'),
        '陕西': url_for('static', filename='json/data/data-1482909918961-BJw1FyZHg.json'),
        '甘肃': url_for('static', filename='json/data/data-1482909780863-r1aIdyWHl.json'),
        '青海': url_for('static', filename='json/data/data-1482909853618-B1IiOyZSl.json'),
        '宁夏': url_for('static', filename='json/data/data-1482909848690-HJWiuy-Bg.json'),
        '新疆': url_for('static', filename='json/data/data-1482909952731-B1YZKkbBx.json'),
        '北京': url_for('static', filename='json/data/data-1482818963027-Hko9SKJrg.json'),
        '天津': url_for('static', filename='json/data/data-1482909944620-r1-WKyWHg.json'),
        '重庆': url_for('static', filename='json/data/data-1482909775470-HJDIdk-Se.json'),
        '香港': url_for('static', filename='json/data/data-1461584707906-r1hSmtsx.json'),
        '澳门': url_for('static', filename='json/data/data-1482909771696-ByVIdJWBx.json')
    }
    china_map_url = url_for('static', filename='json/data/data-1527045631990-r1dZ0IM1X.json')

    return render_template('knowledge.html', provinces=province_data, china_map_url=china_map_url)


@app.route('/sounds')
def sounds():
    return render_template('sounds.html')


@app.route('/qna')
def qna():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # Fetch user data (including avatar and score) from the database
    user_data = db.reference(f"users/{email_prefix}").get()

    # 如果user_data为None，初始化为空字典
    if user_data is None:
        user_data = {}
        # 创建用户数据
        default_avatar_url = url_for('static', filename='images/default-avatar.jpeg')
        db.reference(f"users/{email_prefix}").set({
            "name": user_info.get('display_name', email_prefix),
            "email": user_info.get('email', ''),
            "avatar": default_avatar_url,
            "score": 0
        })
        user_data['avatar'] = default_avatar_url
        user_data['score'] = 0
        user_data['name'] = user_info.get('display_name', email_prefix)

    # If no avatar is found, set default avatar
    elif 'avatar' not in user_data:
        default_avatar_url = url_for('static', filename='images/default-avatar.jpeg')
        db.reference(f"users/{email_prefix}").update({"avatar": default_avatar_url})
        user_data['avatar'] = default_avatar_url
    
    # 确保score存在于user_data中
    if 'score' not in user_data:
        user_data['score'] = 0
        db.reference(f"users/{email_prefix}").update({"score": 0})
    
    # 确保name存在于user_data中
    if 'name' not in user_data:
        user_data['name'] = user_info.get('display_name', email_prefix)
        db.reference(f"users/{email_prefix}").update({"name": user_data['name']})

    return render_template('qna.html', 
                          user_score=user_data.get('score', 0),
                          user_avatar=user_data.get('avatar', url_for('static', filename='images/default-avatar.jpeg')),
                          user_name=user_data.get('name', email_prefix))


@app.route('/guess_who_page')
def guess_who():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # Fetch user data (including avatar and score) from the database
    user_data = db.reference(f"users/{email_prefix}").get()

    # Default user info if not found in the session or database
    user_info = {
        'uid': user_info.get('uid', 'unknown'),
        'email': user_info.get('email', 'unknown'),
        'display_name': user_info.get('display_name', 'Guest'),
        'avatar': user_data.get('avatar', 'default_avatar.png'),
        'score': user_data.get('score', 0)  # Assuming there's a score field in your database
    }

    return render_template('guess_who_page.html', user_info=user_info)


@app.route('/get_random_animal')
def get_random_animal():
    animals_ref = db.reference('animals')
    animals_data = animals_ref.get()

    if not animals_data:
        return jsonify({"status": "error", "message": "No animals found"}), 400

    animals_list = list(animals_data.values())
    return jsonify({"status": "success", "animals": animals_list})


@app.route('/matching_game_page')
def matching_game():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # Fetch user data (including avatar and score) from the database
    user_data = db.reference(f"users/{email_prefix}").get()

    # Default user info if not found in the session or database
    user_info = {
        'uid': user_info.get('uid', 'unknown'),
        'email': user_info.get('email', 'unknown'),
        'display_name': user_info.get('display_name', 'Guest'),
        'avatar': user_data.get('avatar', 'default_avatar.png'),
        'score': user_data.get('score', 0)  # Assuming there's a score field in your database
    }

    return render_template('matching_game_page.html', user_info=user_info)


@app.route('/get_matching_game_data')
def get_matching_game_data():
    animals_ref = db.reference('animals')
    animals_data = animals_ref.get()

    if not animals_data:
        return jsonify({"status": "error", "message": "No animals found"}), 400

    animals_list = list(animals_data.values())
    random_animals = random.sample(animals_list, 4)

    random_names = [animal['cn'] for animal in random_animals]
    random.shuffle(random_names)

    return jsonify({
        "status": "success",
        "animals": random_animals,
        "names": random_names
    })


@app.route('/draw')
def draw():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # Fetch user data (including avatar and score) from the database
    user_data = db.reference(f"users/{email_prefix}").get()

    # Default user info if not found in the session or database
    user_info = {
        'uid': user_info.get('uid', 'unknown'),
        'email': user_info.get('email', 'unknown'),
        'display_name': user_info.get('display_name', 'Guest'),
        'avatar': user_data.get('avatar', 'default_avatar.png'),
        'score': user_data.get('score', 0)  # Assuming there's a score field in your database
    }

    return render_template('draw.html', user_info=user_info)


@app.route('/puzzle')
def puzzle():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # Fetch user data (including avatar and score) from the database
    user_data = db.reference(f"users/{email_prefix}").get()

    # Default user info if not found in the session or database
    user_info = {
        'uid': user_info.get('uid', 'unknown'),
        'email': user_info.get('email', 'unknown'),
        'display_name': user_info.get('display_name', 'Guest'),
        'avatar': user_data.get('avatar', 'default_avatar.png'),
        'score': user_data.get('score', 0)  # Assuming there's a score field in your database
    }

    return render_template('puzzle.html', user_info=user_info)


@app.route('/challenge_mode_page')
def challenge_mode():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # Fetch user data (including avatar and score) from the database
    user_data_ref = db.reference(f"users/{email_prefix}")
    user_data = user_data_ref.get()

    # Default user info if not found in the session or database
    if 'history_best' not in user_data:
        # Initialize history_best if not present
        user_data_ref.update({'history_best': 0})
        history_best = 0
    else:
        history_best = user_data['history_best']

    user_info = {
        'uid': user_info.get('uid', 'unknown'),
        'email': user_info.get('email', 'unknown'),
        'display_name': user_info.get('display_name', 'Guest'),
        'avatar': user_data.get('avatar', 'default_avatar.png'),
        'score': user_data.get('score', 0),
        'history_best': history_best  # Include history_best in user info
    }

    return render_template('challenge_mode_page.html', user_info=user_info)


@app.route('/update_history_best', methods=['POST'])
def update_history_best():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    # Get the user email and history_best from the request
    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    new_history_best = request.json.get('history_best', 0)

    # Fetch the user's existing history_best from the database
    user_data_ref = db.reference(f'users/{email_prefix}')
    user_data = user_data_ref.get()

    # Check if the user's history_best exists
    if 'history_best' not in user_data:
        # If not, initialize it with the new value (0 initially)
        user_data_ref.update({'history_best': new_history_best})
    else:
        # Only update if the new value is greater than the existing one
        current_best = user_data.get('history_best', 0)
        if new_history_best > current_best:
            user_data_ref.update({'history_best': new_history_best})

    return jsonify({"status": "success", "history_best": new_history_best}), 200


@app.route('/get_leaderboard')
def get_leaderboard():
    # Fetch all users' highest scores (history_best)
    users_ref = db.reference('users')
    users_data = users_ref.get()

    if not users_data:
        return jsonify({"status": "error", "message": "No user data found"}), 404

    leaderboard = []

    # Collect the leaderboard with usernames and their history_best values
    for user_key, user_data in users_data.items():
        # Only consider users who have a valid history_best
        if 'history_best' in user_data and user_data['history_best'] > 0:
            # Try getting the user's display_name, if it doesn't exist, fallback to 'name' or 'username'
            display_name = user_data.get('display_name') or user_data.get('name') or user_data.get(
                'username') or 'Guest'

            leaderboard.append({
                'username': display_name,
                'history_best': user_data['history_best']
            })

    # Sort leaderboard by history_best in descending order
    leaderboard = sorted(leaderboard, key=lambda x: x['history_best'], reverse=True)

    return jsonify({"status": "success", "leaderboard": leaderboard}), 200


@app.route('/map')
def map_page():  # 为了清晰起见，重命名为 map_page
    province_data = {
        '上海': url_for('static', filename='json/data/data-1482909900836-H1BC_1WHg.json'),
        '河北': url_for('static', filename='json/data/data-1482909799572-Hkgu_yWSg.json'),
        '山西': url_for('static', filename='json/data/data-1482909909703-SyCA_JbSg.json'),
        '内蒙古': url_for('static', filename='json/data/data-1482909841923-rkqqdyZSe.json'),
        '辽宁': url_for('static', filename='json/data/data-1482909836074-rJV9O1-Hg.json'),
        '吉林': url_for('static', filename='json/data/data-1482909832739-rJ-cdy-Hx.json'),
        '黑龙江': url_for('static', filename='json/data/data-1482909803892-Hy4__J-Sx.json'),
        '江苏': url_for('static', filename='json/data/data-1482909823260-HkDtOJZBx.json'),
        '浙江': url_for('static', filename='json/data/data-1482909960637-rkZMYkZBx.json'),
        '安徽': url_for('static', filename='json/data/data-1482909768458-HJlU_yWBe.json'),
        '福建': url_for('static', filename='json/data/data-1478782908884-B1H6yezWe.json'),
        '江西': url_for('static', filename='json/data/data-1482909827542-r12YOJWHe.json'),
        '山东': url_for('static', filename='json/data/data-1482909892121-BJ3auk-Se.json'),
        '河南': url_for('static', filename='json/data/data-1482909807135-SJPudkWre.json'),
        '湖北': url_for('static', filename='json/data/data-1482909813213-Hy6u_kbrl.json'),
        '湖南': url_for('static', filename='json/data/data-1482909818685-H17FOkZSl.json'),
        '广东': url_for('static', filename='json/data/data-1482909784051-BJgwuy-Sl.json'),
        '广西': url_for('static', filename='json/data/data-1482909787648-SyEPuJbSg.json'),
        '海南': url_for('static', filename='json/data/data-1482909796480-H12P_J-Bg.json'),
        '四川': url_for('static', filename='json/data/data-1482909931094-H17eKk-rg.json'),
        '贵州': url_for('static', filename='json/data/data-1482909791334-Bkwvd1bBe.json'),
        '云南': url_for('static', filename='json/data/data-1482909957601-HkA-FyWSx.json'),
        '西藏': url_for('static', filename='json/data/data-1482927407942-SkOV6Qbrl.json'),
        '陕西': url_for('static', filename='json/data/data-1482909918961-BJw1FyZHg.json'),
        '甘肃': url_for('static', filename='json/data/data-1482909780863-r1aIdyWHl.json'),
        '青海': url_for('static', filename='json/data/data-1482909853618-B1IiOyZSl.json'),
        '宁夏': url_for('static', filename='json/data/data-1482909848690-HJWiuy-Bg.json'),
        '新疆': url_for('static', filename='json/data/data-1482909952731-B1YZKkbBx.json'),
        '北京': url_for('static', filename='json/data/data-1482818963027-Hko9SKJrg.json'),
        '天津': url_for('static', filename='json/data/data-1482909944620-r1-WKyWHg.json'),
        '重庆': url_for('static', filename='json/data/data-1482909775470-HJDIdk-Se.json'),
        '香港': url_for('static', filename='json/data/data-1461584707906-r1hSmtsx.json'),
        '澳门': url_for('static', filename='json/data/data-1482909771696-ByVIdJWBx.json')
    }
    china_map_url = url_for('static', filename='json/data/data-1527045631990-r1dZ0IM1X.json')

    return render_template('map.html', provinces=province_data, china_map_url=china_map_url)


@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user', None)  # Remove user data from session
    return jsonify({"status": "success", "redirect_url": url_for('index')}), 200


@app.route('/multiplayer')
def multiplayer():
    return render_template('multiplayer.html')


# 获取下一个房间 ID
def get_next_room_id():
    rooms_ref = db.reference('rooms')
    rooms = rooms_ref.get()

    if not rooms:
        return 1

    if isinstance(rooms, list):
        rooms = {str(i): room for i, room in enumerate(rooms)}

    return max(map(int, rooms.keys())) + 1  # 获取最大房间 ID 并递增


# 获取下一个玩家 ID
def get_next_player_id(room_id):
    players_ref = db.reference(f'rooms/{room_id}/players')
    players = players_ref.get()
    if not players:
        return "player1"
    return f"player{len(players) + 1}"


# **创建房间**
@app.route('/create_room', methods=['POST'])
def create_room():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    room_name = data.get("room_name")
    room_password = data.get("room_password")
    is_public = data.get("is_public")

    room_id = str(get_next_room_id())

    db.reference(f'rooms/{room_id}').set({
        'room_name': room_name,
        'password': None if is_public else room_password,
        'is_public': is_public,
        'players': {}
    })

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    user_data = db.reference(f'users/{email_prefix}').get()

    if not user_data:
        return jsonify({"status": "error", "message": "User data not found"}), 404

    user_name = user_data.get('name', email_prefix)
    avatar_url = user_data.get('avatar', url_for('static', filename='images/default-avatar.jpeg'))
    user_id = user_data.get('KEYID', email_prefix)  # 获取用户的 KEYID 作为 userID

    player_id = get_next_player_id(room_id)
    db.reference(f'rooms/{room_id}/players/{player_id}').set({
        'username': user_name,
        'avatar': avatar_url,
        'ready': False,
        'userID': user_id  # 添加 userID
    })

    return jsonify(
        {"status": "success", "room_id": room_id, "redirect_url": url_for('room_page', room_id=room_id)}
    ), 200


# **加入房间**
@app.route('/join_room', methods=['POST'])
def join_room():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    room_id = data.get("room_id")
    entered_password = data.get("entered_password", "")

    room_ref = db.reference(f'rooms/{room_id}')
    room_data = room_ref.get()

    if not room_data:
        return jsonify({"status": "error", "message": "Room not found"}), 404

    if not room_data['is_public'] and room_data['password'] != entered_password:
        return jsonify({"status": "error", "message": "Incorrect password"}), 403

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    user_data = db.reference(f'users/{email_prefix}').get()

    if not user_data:
        return jsonify({"status": "error", "message": "User data not found"}), 404

    user_name = user_data.get('name', email_prefix)
    avatar_url = user_data.get('avatar', url_for('static', filename='images/default-avatar.jpeg'))
    user_id = user_data.get('KEYID', email_prefix)  # 获取 KEYID 作为 userID

    players = room_data.get('players', {})

    # **检查用户是否已在房间中**
    for player in players.values():
        if player.get("userID") == user_id:
            return jsonify({"status": "error", "message": "You have already joined this room"}), 400

    player_id = get_next_player_id(room_id)

    db.reference(f'rooms/{room_id}/players/{player_id}').set({
        'username': user_name,
        'avatar': avatar_url,
        'ready': False,
        'userID': user_id  # 存入 userID
    })

    return jsonify({"status": "success", "redirect_url": url_for('room_page', room_id=room_id)}), 200


@app.route('/get_rooms', methods=['GET'])
def get_rooms():
    rooms_ref = db.reference('rooms')
    rooms = rooms_ref.get()

    if not rooms:
        return jsonify({"status": "success", "rooms": []}), 200

    room_list = []

    if isinstance(rooms, list):  # 检查 rooms 是否为列表
        for i, room in enumerate(rooms):
            if room is None:
                continue
            room_list.append({
                'room_id': str(i),  # Firebase 数组索引从 1 开始
                'room_name': room.get('room_name', 'Unknown'),
                'is_public': room.get('is_public', True),
                'players': room.get('players', {})
            })
    else:  # rooms 是一个字典
        for room_id, room_data in rooms.items():
            if room_data is None:
                continue
            room_list.append({
                'room_id': str(room_id),  # 直接使用字典的 key 作为 room_id
                'room_name': room_data.get('room_name', 'Unknown'),
                'is_public': room_data.get('is_public', True),
                'players': room_data.get('players', {})
            })

    return jsonify({"status": "success", "rooms": room_list}), 200


@app.route('/get_room/<room_id>', methods=['GET'])
def get_room(room_id):
    room_ref = db.reference(f'rooms/{room_id}')
    room_data = room_ref.get()

    if not room_data:
        return jsonify({"status": "error", "message": "Room not found"}), 404

    for player_id, player in room_data.get('players', {}).items():
        email_prefix = player['username']
        user_data = db.reference(f'users/{email_prefix}').get()

        if user_data:
            room_data['players'][player_id]['username'] = user_data.get('name', email_prefix)

    return jsonify({"status": "success", "room_data": room_data}), 200


@app.route('/room/<room_id>')
def room_page(room_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    room_ref = db.reference(f'rooms/{room_id}')
    room_data = room_ref.get()

    if not room_data:
        return redirect(url_for('qna'))

    return render_template('room.html', room_id=room_id, room_data=room_data)


@app.route('/set_ready', methods=['POST'])
def set_ready():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    room_id = data.get("room_id")

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    user_data = db.reference(f'users/{email_prefix}').get()
    if not user_data:
        return jsonify({"status": "error", "message": "User data not found"}), 404

    user_name = user_data.get('name', email_prefix)

    players_ref = db.reference(f'rooms/{room_id}/players')
    players = players_ref.get()

    if players:
        for player_key, player_data in players.items():
            if player_data["username"] == user_name:
                current_ready_status = player_data.get("ready", False)
                new_ready_status = not current_ready_status

                db.reference(f'rooms/{room_id}/players/{player_key}').update({"ready": new_ready_status})

                return jsonify({"status": "success", "new_ready_status": new_ready_status}), 200

    return jsonify({"status": "error", "message": "Player not found"}), 404


@app.route('/leave_room', methods=['POST'])
def leave_room():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    room_id = data.get("room_id")
    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    user_data = db.reference(f'users/{email_prefix}').get()
    user_name = user_data.get('name', email_prefix)
    room_ref = db.reference(f'rooms/{room_id}')
    room_data = room_ref.get()

    if not room_data:
        return jsonify({"status": "error", "message": "Room not found"}), 404

    players_ref = db.reference(f'rooms/{room_id}/players')
    players = players_ref.get()

    for player_key, player_data in players.items():
        if player_data["username"] == user_name:
            db.reference(f'rooms/{room_id}/players/{player_key}').delete()
            break

    if not db.reference(f'rooms/{room_id}/players').get():
        db.reference(f'rooms/{room_id}').delete()
    else:
        if room_data.get("owner") == user_name:
            new_owner = sorted(players.keys())[0]
            new_owner_name = players[new_owner]["username"]
            db.reference(f'rooms/{room_id}').update({"owner": new_owner_name})

    return jsonify({"status": "success"}), 200


@app.route('/start_game', methods=['POST'])
def start_game():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    room_id = data.get("room_id")

    room_ref = db.reference(f'rooms/{room_id}')
    room_data = room_ref.get()

    if not room_data:
        return jsonify({"status": "error", "message": "Room not found"}), 404

    # 初始化每个玩家的 gamescore
    players_ref = db.reference(f'rooms/{room_id}/players')
    players = players_ref.get()
    if players:
        for player_key in players.keys():
            db.reference(f'rooms/{room_id}/players/{player_key}').update({"gamescore": 0})

    return jsonify({"status": "success"}), 200


@app.route('/gamestart/<room_id>')
def gamestart_page(room_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    room_ref = db.reference(f'rooms/{room_id}')
    room_data = room_ref.get()

    if not room_data:
        return redirect(url_for('qna'))

    return render_template('gamestart.html', room_id=room_id, room_data=room_data)


@app.route('/increase_score', methods=['POST'])
def increase_score():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    room_id = data.get('room_id')

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    user_data = db.reference(f'users/{email_prefix}').get()

    user_name = user_data.get('name', email_prefix)

    players_ref = db.reference(f'rooms/{room_id}/players')
    players = players_ref.get()

    if players:
        for player_key, player_data in players.items():
            if player_data["username"] == user_name:
                new_score = player_data.get("gamescore", 0) + 1
                db.reference(f'rooms/{room_id}/players/{player_key}').update({"gamescore": new_score})

                # Update the user's score in the users table (for high score)
                current_user_score = user_data.get('score', 0)
                new_user_score = current_user_score + 1
                db.reference(f'users/{email_prefix}').update({'score': new_user_score})

                # Update high score if needed
                high_score = user_data.get('high_score', 0)
                if new_user_score > high_score:
                    db.reference(f'users/{email_prefix}').update({'high_score': new_user_score})

                return jsonify({"status": "success", "player_id": player_key, "new_score": new_score}), 200

    return jsonify({"status": "error", "message": "Player not found"}), 404


@app.route('/shop')
def shop():
    if 'user' not in session:
        return redirect(url_for('login'))

    # 获取商店中的宠物种类
    shop_ref = db.reference('shop')
    shop_data = shop_ref.get()

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]  # 作为 KEYID
    user_ref = db.reference(f"users/{email_prefix}")
    user_data = user_ref.get()

    # 初始化 `score` 仅当它不存在时
    if not user_data:
        user_data = {"name": "Unknown", "score": 0}
        user_ref.set(user_data)  # 设置默认值
    elif "score" not in user_data:
        user_data["score"] = 0
        user_ref.update({"score": 0})

    return render_template('shop.html', user_info=user_info, user_data=user_data, shop_data=shop_data)


# 新增 API：获取用户最新积分
@app.route('/get_score', methods=['GET'])
def get_score():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    email_prefix = session['user']['email'].split('@')[0]
    user_ref = db.reference(f"users/{email_prefix}")
    user_data = user_ref.get()

    if not user_data or "score" not in user_data:
        return jsonify({"status": "error", "message": "Score not found"}), 404

    return jsonify({"status": "success", "score": user_data["score"]}), 200


@app.route('/collection')
def collection():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    user_ref = db.reference(f"users/{email_prefix}")
    user_data = user_ref.get()

    # 如果没有 user_data，初始化新用户
    if user_data is None:
        user_data = {"name": "Unknown", "score": 0}
        user_ref.set(user_data)  # 初始化用户数据

    # 如果没有 score 字段，则初始化为 0
    if "score" not in user_data:
        user_data["score"] = 0
        user_ref.update({"score": 0})  # 确保 score 初始化为0

    # 获取用户的宠物 ID 列表
    pet_ids = user_data.get('pets', [])
    # 获取宠物数据
    pets_data = {}
    for pet_id in pet_ids:
        pet_ref = db.reference(f"pets/{pet_id}")
        pet_info = pet_ref.get()
        if pet_info:  # 确保宠物信息存在
            if pet_info.get('hunger', 100) == 0:
                pet_ref.update({"in_storage": True})

            # 确保使用10分钟的更新间隔
            last_update = datetime.strptime(
                pet_info.get('last_update', datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')), '%Y-%m-%d %H:%M:%S')
            next_update = last_update + timedelta(minutes=10)
            pet_info['next_update_time'] = next_update.strftime('%Y-%m-%d %H:%M:%S')

            pets_data[pet_id] = pet_info

    # 打印 pets_data 以进行调试
    print(f"Retrieved pets data for {email_prefix}: {pets_data}")

    # 返回模板
    return render_template('collection.html', user_info=user_info, user_data=user_data, pets=pets_data)


@app.route('/update_pet_name', methods=['POST'])
def update_pet_name():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "用户未登录"}), 403

    data = request.get_json()
    pet_id = data.get('petId')
    new_name = data.get('newName')

    if not pet_id or not new_name:
        return jsonify({"status": "error", "message": "宠物ID或新名称不能为空"}), 400

    # 更新宠物名称
    pet_ref = db.reference(f"pets/{pet_id}")
    pet_data = pet_ref.get()

    if not pet_data:
        return jsonify({"status": "error", "message": "宠物未找到"}), 404

    # 更新宠物名称
    pet_ref.update({"name": new_name})

    return jsonify({"status": "success", "message": "宠物名称更新成功"}), 200


def update_pet_status():
    pets_ref = db.reference("pets")
    pets_data = pets_ref.get()

    if pets_data:
        for pet_id, pet in pets_data.items():
            # 获取上次更新时间
            last_update_str = pet.get('last_update')
            if last_update_str:
                last_update = datetime.strptime(last_update_str, '%Y-%m-%d %H:%M:%S')
                # 检查是否已经过了10分钟
                time_diff = datetime.utcnow() - last_update
                if time_diff.total_seconds() < 600:  # 600秒 = 10分钟
                    continue  # 如果没有过10分钟，跳过这个宠物的更新

            # 获取当前的饥饿度和心情值
            hunger = pet.get("hunger", 100)
            mood = pet.get("mood", 100)

            # 模拟饥饿度和心情值的下降
            new_hunger = max(hunger - 5, 0)  # 降低饥饿度，但不小于0
            new_mood = max(mood - 2, 0)  # 降低心情值，但不小于0

            # 计算下次更新时间（10分钟后）
            next_update_time = datetime.utcnow() + timedelta(minutes=10)
            current_time = datetime.utcnow()

            # 更新宠物的状态和下一次更新时间
            pets_ref.child(pet_id).update({
                "hunger": new_hunger,
                "mood": new_mood,
                "next_update_time": next_update_time.strftime('%Y-%m-%d %H:%M:%S'),
                "last_update": current_time.strftime('%Y-%m-%d %H:%M:%S')
            })


# 修改调度器设置
scheduler.add_job(func=update_pet_status, trigger="interval", minutes=1)  # 保持每分钟检查一次


@app.route('/feed_pet', methods=['POST'])
def feed_pet():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "用户未登录"}), 403

    data = request.get_json()
    pet_id = data.get('petId')

    # 获取当前用户的积分
    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]  # 获取 email 前缀作为 keyId
    user_ref = db.reference(f"users/{email_prefix}")
    user_data = user_ref.get()

    current_score = user_data.get("score", 0)

    # 检查用户是否有足够的积分
    if current_score < 1:
        return jsonify({"status": "error", "message": "Insufficient score"}), 400

    # 获取宠物信息
    pet_ref = db.reference(f"pets/{pet_id}")
    pet_data = pet_ref.get()

    if not pet_data:
        return jsonify({"status": "error", "message": "Pet not found"}), 404

    # 更新宠物的饥饿值并进行判断
    new_hunger = pet_data.get("hunger", 0) + 10  # 增加 10 的饥饿值
    new_hunger = min(new_hunger, 100)  # 饥饿值不能超过100
    pet_ref.update({"hunger": new_hunger})

    # 如果饥饿值为0，将宠物放入仓库，无法被取出
    if new_hunger == 0:
        pet_ref.update({"in_storage": True})

    # 扣除积分
    new_score = current_score - 1
    user_ref.update({"score": new_score})

    return jsonify({
        "status": "success",
        "score": new_score,
        "pet_hunger": new_hunger,
        "pet_in_storage": pet_data.get("in_storage", False)
    }), 200


@app.route('/add_score', methods=['POST'])
def add_score():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403
    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    user_ref = db.reference(f"users/{email_prefix}")
    user_data = user_ref.get()

    current_score = user_data.get("score", 0) if user_data else 0
    new_score = current_score + 1
    user_ref.update({"score": new_score})

    return jsonify({"status": "success", "score": new_score}), 200


@app.route('/buy_pet', methods=['POST'])
def buy_pet():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "用户未登录"}), 403

    data = request.get_json()
    pet_type = data.get('petType')  # 获取宠物种类

    # 获取用户信息
    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]  # 获取 email 前缀作为 keyId

    # 获取宠物数据（从商店表中）
    pet_ref = db.reference(f"shop/{pet_type}")
    pet_data = pet_ref.get()

    if not pet_data:
        return jsonify({"status": "error", "message": "宠物种类无效"}), 400

    pet_name = pet_data['name']
    pet_image = pet_data.get('image', '')

    # 获取用户当前金币
    user_ref = db.reference(f"users/{email_prefix}")
    user_data = user_ref.get()

    if not user_data:
        return jsonify({"status": "error", "message": "无法找到用户数据"}), 404

    user_score = user_data.get('score', 0)
    # **确保将 pet_data['price'] 转换为 int**

    pet_price = int(pet_data['price'])  # 将价格转换为整数

    # 检查用户是否有足够金币
    if user_score < pet_price:
        return jsonify({"status": "error", "message": "金币不足"}), 400

    # 扣除用户金币
    new_score = user_score - pet_price
    user_ref.update({"score": new_score})

    # 为宠物生成一个唯一的 ID
    pet_id = f"{email_prefix}_pet_{int(datetime.now().timestamp())}"  # 使用时间戳使宠物 ID 唯一

    # 添加宠物到 pets 表
    pet_ref = db.reference(f"pets/{pet_id}")
    pet_ref.set({
        "user_uid": email_prefix,  # 关联用户 keyId
        "pet_id": pet_id,  # 宠物 ID
        "name": pet_name,  # 宠物名称
        "type": pet_type,  # 宠物类型
        "age": 0,  # 初始年龄为 0
        "hunger": 100,  # 初始饥饿度为 100
        "mood": 100,  # 初始心情值为 100
        "status": "running",  # 初始状态
        "in_storage": True,  # 默认在仓库
        "image": pet_image  # 添加宠物图片引用
    })

    # 将宠物 ID 添加到用户数据的宠物列表
    if 'pets' not in user_data:
        user_data['pets'] = []  # 初始化宠物列表
    user_data['pets'].append(pet_id)  # 添加新宠物 ID
    user_ref.update({"pets": user_data['pets']})  # 更新用户数据

    return jsonify({
        "status": "success",
        "newScore": new_score,
        "petName": pet_name
    }), 200


@app.route('/get_user_pets', methods=['GET'])
def get_user_pets():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "用户未登录"}), 403

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    user_ref = db.reference(f"users/{email_prefix}")
    user_data = user_ref.get()

    if not user_data:
        return jsonify({"status": "error", "message": "无法找到用户数据"}), 404

    pet_ids = user_data.get('pets', [])
    pets_data = []

    for pet_id in pet_ids:
        pet_ref = db.reference(f"pets/{pet_id}")
        pet_info = pet_ref.get()
        if pet_info:  # 确保宠物信息存在
            pets_data.append(pet_info)

    return jsonify({"status": "success", "pets": pets_data}), 200


@app.route('/update_pet_storage', methods=['POST'])
def update_pet_storage():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "用户未登录"}), 403

    data = request.get_json()
    pet_id = data.get('petId')

    # 使用 pet_id 获取宠物信息
    pet_ref = db.reference(f"pets/{pet_id}")
    pet_data = pet_ref.get()

    # 确保宠物存在
    if not pet_data:
        return jsonify({"status": "error", "message": "宠物未找到"}), 404

    # 更新宠物 in_storage 状态为 false
    pet_ref.update({"in_storage": False})

    return jsonify({"status": "success", "petData": pet_data}), 200


@app.route('/set_pet_in_storage', methods=['POST'])
def set_pet_in_storage():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "用户未登录"}), 403

    data = request.get_json()
    pet_id = data.get('petId')

    # 使用 pet_id 获取宠物信息，确保宠物存在
    pet_ref = db.reference(f"pets/{pet_id}")
    pet_data = pet_ref.get()

    if not pet_data:
        return jsonify({"status": "error", "message": "宠物未找到"}), 404

    # 更新宠物 in_storage 状态为 true
    pet_ref.update({"in_storage": True})

    return jsonify({"status": "success"}), 200


@app.route('/community')
def community():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('community.html')


from datetime import datetime


# 确保 `posts` 表存在
def initialize_posts_table():
    posts_ref = db.reference('posts')
    if posts_ref.get() is None:
        print("Creating 'posts' table in Firebase...")
        posts_ref.set({})


initialize_posts_table()

UPLOAD_FOLDER = "static/images/post"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'avi'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 确保目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/create_post', methods=['GET', 'POST'])
def create_post():
    if 'user' not in session:
        return redirect(url_for('login'))

    if request.method == 'GET':
        return render_template('create_post.html')

    # POST method handling
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    file = request.files.get("image")

    if not content and not file:
        return jsonify({"status": "error", "message": "Post must have content or an image"}), 400

    email_prefix = session['user']['email'].split('@')[0]
    user_data = db.reference(f'users/{email_prefix}').get()

    if not user_data:
        return jsonify({"status": "error", "message": "User data not found"}), 404

    username = user_data.get("name", email_prefix)

    # 获取当前的最大 post_id
    post_counter_ref = db.reference('post_counter')
    post_id = post_counter_ref.get() or 0
    post_counter_ref.set(post_id + 1)  # 更新 post_counter，使其自增

    img_filename = None
    if file and allowed_file(file.filename):
        img_filename = f"{post_id}_{secure_filename(file.filename)}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], img_filename))

    post_data = {
        "post_id": post_id,
        "keyid": email_prefix,
        "username": username,
        "title": title,
        "content": content,
        "img": img_filename,
        "like": 0,
        "timestamp": datetime.now().isoformat()
    }

    db.reference(f'posts/{post_id}').set(post_data)

    return jsonify({"status": "success", "message": "Post created successfully", "post": post_data}), 200


@app.route('/create_post_page')
def create_post_page():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('create_post.html')


@app.route('/submit_post', methods=['POST'])
def submit_post():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    try:
        # 获取表单数据
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        files = request.files.getlist("media")

        # 验证数据
        if not title:
            return jsonify({"status": "error", "message": "标题不能为空"}), 400
        if not content:
            return jsonify({"status": "error", "message": "内容不能为空"}), 400

        # 获取用户信息
        email_prefix = session['user']['email'].split('@')[0]
        user_data = db.reference(f'users/{email_prefix}').get()
        if not user_data:
            return jsonify({"status": "error", "message": "无法获取用户信息"}), 404

        username = user_data.get("name", email_prefix)
        avatar = user_data.get("avatar", "/static/images/default-avatar.jpeg")

        # 生成帖子ID
        post_counter_ref = db.reference('post_counter')
        post_id = post_counter_ref.get() or 0
        post_counter_ref.set(post_id + 1)

        # 处理媒体文件
        media_filenames = []
        for file in files:
            if file and allowed_file(file.filename):
                # 生成安全的文件名
                filename = f"{post_id}_{secure_filename(file.filename)}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                # 确保上传目录存在
                os.makedirs(os.path.dirname(file_path), exist_ok=True)

                # 保存文件
                file.save(file_path)
                media_filenames.append(filename)

        # 创建帖子数据
        post_data = {
            "post_id": post_id,
            "keyid": email_prefix,
            "username": username,
            "avatar": avatar,
            "title": title,
            "content": content,
            "media": media_filenames,
            "like": 0,
            "liked": False,
            "timestamp": datetime.now().isoformat(),
            "comments": []
        }

        # 保存到数据库
        db.reference(f'posts/{post_id}').set(post_data)

        return jsonify({
            "status": "success",
            "message": "发布成功",
            "post": post_data
        }), 200

    except Exception as e:
        print(f"Error in submit_post: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"发布失败: {str(e)}"
        }), 500


@app.route('/get_posts', methods=['GET'])
def get_posts():
    try:
        posts_ref_data = db.reference('posts').get()
        if not posts_ref_data:
            return jsonify({"status": "success", "posts": []})

        email_prefix = session.get('user', {}).get('email', '').split('@')[0]
        posts_list = []

        # Handle both dictionary and list structures from Firebase
        items_to_iterate = []
        if isinstance(posts_ref_data, dict):
            items_to_iterate = posts_ref_data.items()
        elif isinstance(posts_ref_data, list):
            items_to_iterate = [(str(i), post) for i, post in enumerate(posts_ref_data) if post is not None]

        search_query = request.args.get('search', '').lower()

        for post_id, post in items_to_iterate:
            if not isinstance(post, dict):
                continue

            # Search Filtering
            title = post.get('title', '').lower()
            content = post.get('content', '').lower()
            keyid_for_search = post.get("keyid")

            username_for_search = ""
            if keyid_for_search:
                user_ref_for_search = db.reference(f'users/{keyid_for_search}').get()
                if user_ref_for_search:
                    username_for_search = user_ref_for_search.get("name", "").lower()

            if search_query and not (
                    search_query in title or
                    search_query in content or
                    (username_for_search and search_query in username_for_search)
            ):
                continue

            # Get like status
            liked = False
            if email_prefix:
                liked_status = db.reference(f'likes/{str(post_id)}/{email_prefix}').get()
                liked = bool(liked_status)

            # Get user info
            keyid = post.get("keyid")
            if not keyid:
                continue

            user_ref = db.reference(f'users/{keyid}').get()
            username = user_ref.get("name", "用户") if user_ref else "用户"
            avatar = user_ref.get("avatar", "/static/images/default-avatar.jpeg")

            # Handle media files
            media_urls = []
            media_files = post.get("media", [])
            if isinstance(media_files, list):
                for filename in media_files:
                    if filename:
                        # If it's already a full URL, use it as is
                        if filename.startswith('/static/'):
                            media_urls.append(filename)
                        else:
                            # Otherwise, add the path prefix
                            media_urls.append(f"/static/images/post/{filename}")
            elif isinstance(media_files, str) and media_files:
                if media_files.startswith('/static/'):
                    media_urls = [media_files]
                else:
                    media_urls = [f"/static/images/post/{media_files}"]

            # Build post data
            post_data = {
                "post_id": str(post_id),
                "title": post.get("title", ""),
                "content": post.get("content", ""),
                "media": media_urls,
                "avatar": avatar,
                "username": username,
                "like": post.get("like", 0),
                "liked": liked,
                "timestamp": post.get("timestamp", "")
            }
            posts_list.append(post_data)

        # Sort by timestamp
        posts_list.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        return jsonify({"status": "success", "posts": posts_list})

    except Exception as e:
        print(f"Error in /get_posts: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Internal server error: {str(e)}"}), 500


@app.route('/get_post_detail/<string:post_id>', methods=['GET'])
def get_post_detail(post_id):
    post_ref = db.reference(f'posts/{post_id}')
    post = post_ref.get()

    if not post:
        return jsonify({"status": "error", "message": "Post not found"}), 404

    # Get user info
    keyid = post.get("keyid")
    if not keyid:
        return jsonify({"status": "error", "message": "Post data is incomplete (missing keyid)"}), 500

    user_ref = db.reference(f'users/{keyid}')
    user = user_ref.get()

    username = "用户"
    avatar = "/static/images/default-avatar.jpeg"
    if user:
        username = user.get("name", "用户")
        avatar = user.get("avatar", "/static/images/default-avatar.jpeg")

    # Handle media files
    media_urls = []
    media_files = post.get("media", [])
    if isinstance(media_files, list):
        for filename in media_files:
            if filename:
                # If it's already a full URL, use it as is
                if filename.startswith('/static/'):
                    media_urls.append(filename)
                else:
                    # Otherwise, add the path prefix
                    media_urls.append(f"/static/images/post/{filename}")
    elif isinstance(media_files, str) and media_files:
        if media_files.startswith('/static/'):
            media_urls = [media_files]
        else:
            media_urls = [f"/static/images/post/{media_files}"]

    # Get comments
    comments_ref = db.reference(f'comments/{post_id}').get()
    comments = []
    if comments_ref:
        comment_items = []
        if isinstance(comments_ref, list):
            comment_items = [c for c in comments_ref if c]
        elif isinstance(comments_ref, dict):
            comment_items = list(comments_ref.values())

        for comment in comment_items:
            if not isinstance(comment, dict):
                continue

            comment_user_id = comment.get("keyid")
            if not comment_user_id:
                continue
            comment_user_ref = db.reference(f'users/{comment_user_id}')
            comment_user = comment_user_ref.get()

            comment_username = "评论者"
            comment_avatar = "/static/images/default-avatar.jpeg"
            if comment_user:
                comment_username = comment_user.get("name", "评论者")
                comment_avatar = comment_user.get("avatar", "/static/images/default-avatar.jpeg")

            comment_data = {
                "content": comment.get("content", ""),
                "timestamp": comment.get("timestamp", ""),
                "username": comment_username,
                "avatar": comment_avatar
            }
            comments.append(comment_data)

        comments.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

    # Add liked field (like get_posts)
    email_prefix = session.get('user', {}).get('email', '').split('@')[0]
    liked = False
    if email_prefix:
        liked_status = db.reference(f'likes/{str(post_id)}/{email_prefix}').get()
        liked = bool(liked_status)

    return jsonify({
        "status": "success",
        "post": {
            "post_id": post_id,
            "content": post.get("content", ""),
            "media": media_urls,
            "like": post.get("like", 0),
            "timestamp": post.get("timestamp", ""),
            "title": post.get("title", ""),
            "username": username,
            "avatar": avatar,
            "comments": comments if comments else [],
            "liked": liked
        }
    })


@app.route('/toggle_like', methods=['POST'])
def toggle_like():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    data = request.get_json()
    post_id = data.get("post_id")

    if not post_id:
        return jsonify({"status": "error", "message": "Post ID is required"}), 400

    email_prefix = session['user']['email'].split('@')[0]

    post_ref = db.reference(f'posts/{post_id}')
    post_data = post_ref.get()

    if not post_data:
        return jsonify({"status": "error", "message": "Post not found"}), 404

    likes_ref = db.reference(f'likes/{post_id}/{email_prefix}')
    liked = likes_ref.get()

    if liked:
        likes_ref.set(False)
        new_like_count = max(post_data["like"] - 1, 0)
    else:
        likes_ref.set(True)
        new_like_count = post_data["like"] + 1

    post_ref.update({"like": new_like_count})

    return jsonify({"status": "success", "liked": not liked, "like_count": new_like_count})


@app.route('/create_comment', methods=['POST'])
def create_comment():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "User not logged in"}), 403

    try:
        post_id = request.form.get("post_id")
        content = request.form.get("content").strip()

        if not post_id or not content:
            return jsonify({"status": "error", "message": "Post ID and content are required"}), 400

        # 使用 email_prefix 作为用户标识符
        email_prefix = session['user']['email'].split('@')[0]
        user_data = db.reference(f'users/{email_prefix}').get()
        if not user_data:
            return jsonify({"status": "error", "message": "User data not found"}), 404

        username = user_data.get('name', email_prefix)
        avatar = user_data.get('avatar', '/static/images/default-avatar.jpeg')

        # 获取当前时间戳
        timestamp = datetime.now().isoformat()

        # 获取下一个评论 ID
        comments_ref = db.reference(f'comments/{post_id}')
        current_comments = comments_ref.get() or {}
        next_comment_id = str(len(current_comments))

        # 创建评论数据
        comment_data = {
            "comment_id": next_comment_id,
            "post_id": post_id,
            "keyid": email_prefix,
            "username": username,
            "content": content,
            "timestamp": timestamp,
            "avatar": avatar
        }

        # 保存评论到数据库
        comments_ref.child(next_comment_id).set(comment_data)

        return jsonify({
            "status": "success",
            "message": "Comment added successfully",
            "comment": comment_data
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/get_comments/<int:post_id>', methods=['GET'])
def get_comments(post_id):
    comments_ref = db.reference(f'comments/{post_id}').get()

    if not comments_ref:
        return jsonify({"status": "success", "comments": []})

    comments_list = []
    # 如果 comments_ref 是一个列表，直接迭代
    for comment in comments_ref:
        comments_list.append(comment)

    return jsonify({"status": "success", "comments": comments_list})


# Define the static folder where animal images are stored
static_folder = "static/images/animals"


def update_animal_photos():
    # Get all animal entries from the database
    animals_ref = db.reference("animals")
    animals_data = animals_ref.get()

    if not animals_data:
        return

    for animal_id, animal in animals_data.items():
        # Check if the image exists in the static folder (supports .jpeg .jpg and .png)
        image_jpeg = os.path.join(static_folder, f"{animal_id}.jpeg")
        image_jpg = os.path.join(static_folder, f"{animal_id}.jpg")
        image_png = os.path.join(static_folder, f"{animal_id}.png")

        if os.path.exists(image_jpeg):
            photo_url = f"/static/images/animals/{animal_id}.jpeg"
        elif os.path.exists(image_jpg):
            photo_url = f"/static/images/animals/{animal_id}.jpg"
        elif os.path.exists(image_png):
            photo_url = f"/static/images/animals/{animal_id}.png"
        else:
            photo_url = "/static/images/default-avatar.jpeg"

        # Update the animal's photo field in the database
        animals_ref.child(animal_id).update({"photo": photo_url})


update_animal_photos()

# Define the static folder where animal sounds are stored
static_audio_folder = "static/sounds"


def update_animal_sounds():
    # Get all animal entries from the database
    animals_ref = db.reference("animals")
    animals_data = animals_ref.get()

    if not animals_data:
        return

    for animal_id, animal in animals_data.items():
        # Check if the sound file exists in the static folder (supports .mp3)
        audio_mp3 = os.path.join(static_audio_folder, f"{animal_id}.mp3")

        current_sound_url = animal.get("soundUrl", "/static/sounds/default-sound.mp3")

        if os.path.exists(audio_mp3):
            new_sound_url = f"/static/sounds/{animal_id}.mp3"
            if current_sound_url != new_sound_url:
                # update url in database
                animals_ref.child(animal_id).update({"soundUrl": new_sound_url})
                print(f"Updated sound URL for animal {animal_id} from {current_sound_url} to {new_sound_url}")
        else:
            if current_sound_url != "/static/sounds/default-sound.mp3":
                animals_ref.child(animal_id).update({"soundUrl": "/static/sounds/default-sound.mp3"})
                print(f"Reset sound URL for animal {animal_id} to default sound")

        if os.path.exists(audio_mp3):
            sound_url = f"/static/sounds/{animal_id}.mp3"
        else:
            sound_url = "/static/sounds/default-sound.mp3"  # Optional default sound

        # Update the animal's sound URL in the database
        animals_ref.child(animal_id).update({"soundUrl": sound_url})


# Call the function to update the sound URLs in the database
update_animal_sounds()

@app.route('/get_animals', methods=['GET'])
def get_animals():
    animals_ref = db.reference("animals")
    animals_data = animals_ref.get()

    if not animals_data:
        return jsonify({"status": "success", "animals": []}), 200

    if isinstance(animals_data, list):
        animals_list = [
            {
                "id": str(index),
                "name": a.get("name", "Unknown"),
                "en": a.get("en", "Unknown"),
                "area": a.get("area", "Unknown"),
                "detail": a.get("detail", "No detail available."),
                "photo": a.get("photo"),
                "type": a.get("type", "Unknown")
            }
            for index, a in enumerate(animals_data)
            if isinstance(a, dict)
        ]
    elif isinstance(animals_data, dict):
        animals_list = [
            {
                "id": key,
                "name": a.get("name", "Unknown"),
                "en": a.get("en", "Unknown"),
                "area": a.get("area", "Unknown"),
                "detail": a.get("detail", "No detail available."),
                "photo": a.get("photo"),
                "type": a.get("type", "Unknown"),
                "soundUrl": a.get("soundUrl", "/static/sounds/default-sound.mp3")
            }
            for key, a in animals_data.items()
        ]
    else:
        return jsonify({"status": "error", "message": "Unexpected data format"}), 500

    return jsonify({"status": "success", "animals": animals_list}), 200


@app.route('/get_animal', methods=['GET'])
def get_animal():
    search_query = request.args.get("search", "").strip().lower()
    selected_type = request.args.get("type", "").strip()

    animals_ref = db.reference("animals")
    animals_data = animals_ref.get()

    if not animals_data:
        return jsonify({"status": "success", "animals": []}), 200

    animals_list = []
    for key, animal in animals_data.items():
        name = animal.get("name", "").lower()
        en = animal.get("en", "").lower()
        cn_name = animal.get("cn", "").lower() if "cn" in animal and animal.get("cn") else ""
        animal_type = animal.get("type", "")

        matches_search = search_query in name or search_query in cn_name or search_query in en
        matches_type = (selected_type == "" or animal_type == selected_type)

        if matches_search and matches_type:
            animals_list.append({
                "id": key,
                "name": animal.get("name", "Unknown"),
                "en": animal.get("en", "Unknown"),
                "cn": animal.get("cn", "未知"),  # 确保 `cn` 有值
                "area": animal.get("area", "Unknown"),
                "photo": animal.get("photo", "/static/images/default-avatar.jpeg"),
                "type": animal.get("type", "Unknown"),
                "soundUrl": animal.get("soundUrl", "/static/sounds/default-sound.mp3")
            })

    return jsonify({"status": "success", "animals": animals_list}), 200


@app.route('/fetch-details/<animal_id>')
def fetch_details(animal_id):
    try:
        animals_ref = db.reference(f'animals/{animal_id}')
        animal = animals_ref.get()

        if not animal or 'detail' not in animal:
            return jsonify({"status": "error", "message": "No detail URL found"}), 404

        url = animal['detail']
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
        }

        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return jsonify({"status": "error", "message": "Failed to retrieve data"}), 500

        # 使用 html.parser 作为解析器
        soup = BeautifulSoup(response.content, "html.parser")

        # 找到页面标题
        title_element = soup.find("h1", {"id": "firstHeading", "class": "firstHeading mw-first-heading"})
        if not title_element:
            return jsonify({"status": "error", "message": "Could not parse page title"}), 500

        page_title = title_element.get_text()

        # 找到完整主体内容
        content_div = soup.find("div", {"id": "mw-content-text", "class": "mw-body-content"})
        if not content_div:
            return jsonify({"status": "error", "message": "Could not parse page content"}), 500

        # 修正所有链接和图片为绝对路径
        for a in content_div.find_all('a', href=True):
            if a['href'].startswith('/'):
                a['href'] = "https://en.wikipedia.org" + a['href']

        for img in content_div.find_all('img', src=True):
            if img['src'].startswith('//'):
                img['src'] = 'https:' + img['src']

        # 找到 "References" 及其后的部分并删除
        references_header = content_div.find("h2", {"id": "References"})
        if references_header:
            delete_flag = False  # 设定删除标记
            for element in content_div.find_all():  # 遍历所有子元素
                if element == references_header:  # 遇到 "References" 触发删除模式
                    delete_flag = True
                if delete_flag:
                    element.decompose()  # 删除所有后续内容

        return jsonify({
            "status": "success",
            "title": page_title,
            "content": str(content_div)
        })
    except Exception as e:
        print(f"Error in fetch_details: {str(e)}")
        return jsonify({"status": "error", "message": f"An error occurred: {str(e)}"}), 500


def is_in_province(area, province):
    if isinstance(area, str):
        provinces = area.split("、")  # 用中文顿号分割省份名称
        return province in provinces
    return False


@app.route('/province/<province>')
def province_detail(province):
    if 'user' not in session:
        return redirect(url_for('login'))

    # 获取省份地图数据
    province_data = {
        '上海': url_for('static', filename='json/data/data-1482909900836-H1BC_1WHg.json'),
        '河北': url_for('static', filename='json/data/data-1482909799572-Hkgu_yWSg.json'),
        '山西': url_for('static', filename='json/data/data-1482909909703-SyCA_JbSg.json'),
        '内蒙古': url_for('static', filename='json/data/data-1482909841923-rkqqdyZSe.json'),
        '辽宁': url_for('static', filename='json/data/data-1482909836074-rJV9O1-Hg.json'),
        '吉林': url_for('static', filename='json/data/data-1482909832739-rJ-cdy-Hx.json'),
        '黑龙江': url_for('static', filename='json/data/data-1482909803892-Hy4__J-Sx.json'),
        '江苏': url_for('static', filename='json/data/data-1482909823260-HkDtOJZBx.json'),
        '浙江': url_for('static', filename='json/data/data-1482909960637-rkZMYkZBx.json'),
        '安徽': url_for('static', filename='json/data/data-1482909768458-HJlU_yWBe.json'),
        '福建': url_for('static', filename='json/data/data-1478782908884-B1H6yezWe.json'),
        '江西': url_for('static', filename='json/data/data-1482909827542-r12YOJWHe.json'),
        '山东': url_for('static', filename='json/data/data-1482909892121-BJ3auk-Se.json'),
        '河南': url_for('static', filename='json/data/data-1482909807135-SJPudkWre.json'),
        '湖北': url_for('static', filename='json/data/data-1482909813213-Hy6u_kbrl.json'),
        '湖南': url_for('static', filename='json/data/data-1482909818685-H17FOkZSl.json'),
        '广东': url_for('static', filename='json/data/data-1482909784051-BJgwuy-Sl.json'),
        '广西': url_for('static', filename='json/data/data-1482909787648-SyEPuJbSg.json'),
        '海南': url_for('static', filename='json/data/data-1482909796480-H12P_J-Bg.json'),
        '四川': url_for('static', filename='json/data/data-1482909931094-H17eKk-rg.json'),
        '贵州': url_for('static', filename='json/data/data-1482909791334-Bkwvd1bBe.json'),
        '云南': url_for('static', filename='json/data/data-1482909957601-HkA-FyWSx.json'),
        '西藏': url_for('static', filename='json/data/data-1482927407942-SkOV6Qbrl.json'),
        '陕西': url_for('static', filename='json/data/data-1482909918961-BJw1FyZHg.json'),
        '甘肃': url_for('static', filename='json/data/data-1482909780863-r1aIdyWHl.json'),
        '青海': url_for('static', filename='json/data/data-1482909853618-B1IiOyZSl.json'),
        '宁夏': url_for('static', filename='json/data/data-1482909848690-HJWiuy-Bg.json'),
        '新疆': url_for('static', filename='json/data/data-1482909952731-B1YZKkbBx.json'),
        '北京': url_for('static', filename='json/data/data-1482818963027-Hko9SKJrg.json'),
        '天津': url_for('static', filename='json/data/data-1482909944620-r1-WKyWHg.json'),
        '重庆': url_for('static', filename='json/data/data-1482909775470-HJDIdk-Se.json'),
        '香港': url_for('static', filename='json/data/data-1461584707906-r1hSmtsx.json'),
        '澳门': url_for('static', filename='json/data/data-1482909771696-ByVIdJWBx.json')
    }

    if province not in province_data:
        return "省份不存在", 404

    # 获取该省份的动物数据
    animals_ref = db.reference("animals")
    animals_data = animals_ref.get()

    # 过滤出指定省份的动物
    province_animals = []
    if isinstance(animals_data, list):
        for animal in animals_data:
            if isinstance(animal, dict) and is_in_province(animal.get("area"), province):
                province_animals.append(animal)
    elif isinstance(animals_data, dict):
        for animal_id, animal in animals_data.items():
            if is_in_province(animal.get("area"), province):
                animal["id"] = animal_id
                province_animals.append(animal)

    # 按保护级别分类动物
    categorized_animals = {
        'Critically Endangered (CR)': [],
        'Endangered (EN)': [],
        'Vulnerable (VU)': [],
        'Near Threatened (NT)': [],
        'Least Concern (LC)': []
    }

    for animal in province_animals:
        animal_type = animal.get('type', '')
        if animal_type == 'CR':
            categorized_animals['Critically Endangered (CR)'].append(animal)
        elif animal_type == 'EN':
            categorized_animals['Endangered (EN)'].append(animal)
        elif animal_type == 'VU':
            categorized_animals['Vulnerable (VU)'].append(animal)
        elif animal_type == 'NT':
            categorized_animals['Near Threatened (NT)'].append(animal)
        elif animal_type == 'LC':
            categorized_animals['Least Concern (LC)'].append(animal)

    # 移除空分类
    categorized_animals = {k: v for k, v in categorized_animals.items() if v}

    return render_template('province_detail.html',
                           province=province,
                           province_map_url=province_data[province],
                           categorized_animals=categorized_animals)


@app.route('/search_user', methods=['POST'])
def search_user():
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': '请先登录'})

    data = request.get_json()
    search_username = data.get('username')

    # 获取当前用户信息
    current_user = session['user']
    current_user_email_prefix = current_user['email'].split('@')[0]

    # 在 Firebase 中搜索用户
    users_ref = db.reference('users')
    users = users_ref.get()

    found_user = None
    if users:
        for key, user in users.items():
            if user.get('name') == search_username:
                # 不能搜索到自己
                if key != current_user_email_prefix:
                    found_user = {
                        'id': key,
                        'username': user.get('name'),
                        'avatar': user.get('avatar', '/static/images/default-avatar.jpeg')
                    }
                    break

    if found_user:
        # 检查是否已经是好友
        friendships_ref = db.reference('friendships')
        friendships = friendships_ref.get() or {}

        if (current_user_email_prefix in friendships and
                found_user['id'] in friendships[current_user_email_prefix]):
            return jsonify({'status': 'error', 'message': 'This user is already your friend'})

        # 检查是否已经发送过好友申请
        friend_requests_ref = db.reference('friend_requests')
        friend_requests = friend_requests_ref.get() or {}

        if (found_user['id'] in friend_requests and
                current_user_email_prefix in friend_requests[found_user['id']]):
            return jsonify({'status': 'error', 'message': 'A friend request has been sent to this user'})

        return jsonify({'status': 'success', 'user': found_user})
    else:
        return jsonify({'status': 'success', 'user': None})


@app.route('/send_friend_request', methods=['POST'])
def send_friend_request():
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': '请先登录'})

    data = request.get_json()
    receiver_id = data.get('receiver_id')

    # 获取当前用户信息
    current_user = session['user']
    sender_id = current_user['email'].split('@')[0]

    # 检查是否已经是好友
    friendships_ref = db.reference('friendships')
    friendships = friendships_ref.get() or {}

    if (sender_id in friendships and
            receiver_id in friendships[sender_id]):
        return jsonify({'status': 'error', 'message': '该用户已经是你的好友'})

    # 检查是否已经发送过好友申请
    friend_requests_ref = db.reference('friend_requests')
    friend_requests = friend_requests_ref.get() or {}

    if (receiver_id in friend_requests and
            sender_id in friend_requests[receiver_id]):
        return jsonify({'status': 'error', 'message': 'A friend request has been sent to this user'})

    # 获取发送者信息
    sender_ref = db.reference(f'users/{sender_id}').get()
    sender_name = sender_ref.get('name', sender_id)
    sender_avatar = sender_ref.get('avatar', '/static/images/default-avatar.jpeg')

    # 创建好友申请
    if receiver_id not in friend_requests:
        friend_requests_ref.child(receiver_id).set({})

    # 使用 ISO 格式的时间戳
    current_time = datetime.now().isoformat()

    friend_requests_ref.child(receiver_id).child(sender_id).set({
        'sender_id': sender_id,
        'sender_name': sender_name,
        'sender_avatar': sender_avatar,
        'status': 'pending',
        'timestamp': current_time
    })

    return jsonify({'status': 'success', 'message': 'The friend request has been sent'})


@app.route('/get_friend_requests', methods=['GET'])
def get_friend_requests():
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': '请先登录'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取好友申请
    friend_requests_ref = db.reference(f'friend_requests/{email_prefix}')
    friend_requests = friend_requests_ref.get() or {}

    # 转换为列表格式
    requests_list = []
    for sender_id, request_data in friend_requests.items():
        if request_data.get('status') == 'pending':
            requests_list.append({
                'user_id': sender_id,
                'username': request_data.get('sender_name'),
                'avatar': request_data.get('sender_avatar', '/static/images/default-avatar.jpeg'),
                'timestamp': request_data.get('timestamp')
            })

    return jsonify({'status': 'success', 'requests': requests_list})


@app.route('/handle_friend_request', methods=['POST'])
def handle_friend_request():
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': '请先登录'})

    data = request.get_json()
    sender_id = data.get('user_id')
    action = data.get('action')

    receiver_info = session['user']
    receiver_email_prefix = receiver_info['email'].split('@')[0]

    if action == 'accept':
        # 更新好友申请状态
        request_ref = db.reference(f'friend_requests/{receiver_email_prefix}/{sender_id}')
        request_ref.update({'status': 'accepted'})

        # 建立双向好友关系
        friendships_ref = db.reference('friendships')

        # 获取双方用户信息
        sender_data = db.reference(f'users/{sender_id}').get()
        receiver_data = db.reference(f'users/{receiver_email_prefix}').get()

        # 更新好友关系
        friendships_ref.child(receiver_email_prefix).child(sender_id).set({
            'username': sender_data.get('name'),
            'avatar': sender_data.get('avatar', '/static/images/default-avatar.jpeg'),
            'timestamp': datetime.now().isoformat()
        })

        friendships_ref.child(sender_id).child(receiver_email_prefix).set({
            'username': receiver_data.get('name'),
            'avatar': receiver_data.get('avatar', '/static/images/default-avatar.jpeg'),
            'timestamp': datetime.now().isoformat()
        })

        # 发送欢迎消息
        messages_ref = db.reference('messages')
        message_id = messages_ref.push().key

        messages_ref.child(message_id).set({
            'sender_id': receiver_email_prefix,
            'receiver_id': sender_id,
            'content': "I have accepted your friend request. Now we can chat!",
            'timestamp': datetime.now().isoformat(),
            'is_read': False
        })

        return jsonify({'status': 'success'})

    return jsonify({'status': 'error', 'message': '无效的操作'})


@app.route('/chat_list', methods=['GET'])
def chat_list():
    if 'user' not in session:
        if request.is_json:
            return jsonify({'status': 'error', 'message': '请先登录'})
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取好友信息
    friendships_ref = db.reference(f'friendships/{email_prefix}')
    friendships = friendships_ref.get() or {}

    # 所有消息
    messages_ref = db.reference('messages')
    all_messages = messages_ref.get() or {}

    chats = []
    for friend_id, friend_info in friendships.items():
        last_message = ''
        last_message_time = ''
        unread_count = 0

        # 将消息列表转换为按时间排序的列表
        friend_messages = []
        for message_id, message in all_messages.items():
            if ((message['sender_id'] == email_prefix and message['receiver_id'] == friend_id) or
                    (message['sender_id'] == friend_id and message['receiver_id'] == email_prefix)):
                friend_messages.append(message)

        # 按时间戳排序消息
        friend_messages.sort(key=lambda x: x['timestamp'])

        # 获取最后一条消息和未读消息数
        for message in friend_messages:
            # 更新最后一条消息
            last_message = message['content']
            last_message_time = message['timestamp']

            # 计算未读消息数
            if (message['receiver_id'] == email_prefix and
                    message['sender_id'] == friend_id and
                    not message.get('is_read', False)):
                unread_count += 1

        chats.append({
            'friend_id': friend_id,
            'username': friend_info.get('username', 'Unknown'),
            'avatar': friend_info.get('avatar', '/static/images/default-avatar.jpeg'),
            'last_message': last_message,
            'last_message_time': last_message_time,
            'unread_count': unread_count
        })

    # 按最后消息时间排序
    chats.sort(key=lambda x: x['last_message_time'] if x['last_message_time'] else '', reverse=True)

    # 返回 JSON 还是 HTML
    if request.is_json:
        return jsonify({'status': 'success', 'chats': chats})
    else:
        return render_template('chat_list.html', chats=chats, datetime=datetime, now=datetime.now())


@app.route('/chat_with_friend/<friend_id>')
def chat_with_friend(friend_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 验证是否是好友关系
    friendships_ref = db.reference(f'friendships/{email_prefix}/{friend_id}')
    friend_info = friendships_ref.get()
    if not friend_info:
        return redirect(url_for('chat_list'))

    # 添加 id 到 friend_info
    friend_info['id'] = friend_id

    # 获取所有消息记录
    messages_ref = db.reference('messages')
    all_messages = messages_ref.get() or {}

    chat_messages = []
    unread_message_ids = []

    for message_id, message in all_messages.items():
        is_relevant = (
                (message['sender_id'] == email_prefix and message['receiver_id'] == friend_id) or
                (message['sender_id'] == friend_id and message['receiver_id'] == email_prefix)
        )

        if is_relevant:
            # 标记未读消息（将 ID 收集，稍后统一更新）
            if message['receiver_id'] == email_prefix and message['sender_id'] == friend_id and not message.get(
                    'is_read'):
                unread_message_ids.append(message_id)

            # 附加发送者信息
            sender_data = db.reference(f'users/{message["sender_id"]}').get()
            if sender_data:
                message['sender_name'] = sender_data.get('name', message['sender_id'])
                message['sender_avatar'] = sender_data.get('avatar', '/static/images/default-avatar.jpeg')

            # 标准化时间戳
            if 'timestamp' in message and ' ' in message['timestamp']:
                message['timestamp'] = message['timestamp'].replace(' ', 'T')

            chat_messages.append(message)

    # 消息按时间排序
    chat_messages.sort(key=lambda x: x['timestamp'])

    # 显示时间分组头（每隔3分钟一组）
    for i, msg in enumerate(chat_messages):
        if i == 0:
            msg['show_time_header'] = True
        else:
            prev_time = datetime.fromisoformat(chat_messages[i - 1]['timestamp'])
            curr_time = datetime.fromisoformat(msg['timestamp'])
            msg['show_time_header'] = (curr_time - prev_time).total_seconds() >= 180

    # 将所有未读消息统一标记为已读（避免重复数据库操作）
    for msg_id in unread_message_ids:
        messages_ref.child(msg_id).update({'is_read': True})

    return render_template(
        'chat_detail.html',
        friend=friend_info,
        messages=chat_messages,
        datetime=datetime,
        now=datetime.now()
    )


@app.route('/send_message', methods=['POST'])
def send_message():
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': '请先登录'})

    data = request.get_json()
    receiver_id = data.get('receiver_id')
    content = data.get('content')

    if not content:
        return jsonify({'status': 'error', 'message': '消息内容不能为空'})

    if not receiver_id:
        return jsonify({'status': 'error', 'message': '接收者ID不能为空'})

    sender_info = session['user']
    sender_email_prefix = sender_info['email'].split('@')[0]

    # 获取发送者信息
    sender_data = db.reference(f'users/{sender_email_prefix}').get()
    if not sender_data:
        return jsonify({'status': 'error', 'message': '发送者信息不存在'})

    # 创建新消息
    messages_ref = db.reference('messages')
    message_id = messages_ref.push().key

    # 使用ISO格式的时间戳，确保与模板中的时间格式一致
    current_time = datetime.now().isoformat()

    message_data = {
        'sender_id': sender_email_prefix,
        'receiver_id': receiver_id,
        'content': content,
        'timestamp': current_time,
        'is_read': False,
        'sender_name': sender_data.get('name', sender_email_prefix),
        'sender_avatar': sender_data.get('avatar', '/static/images/default-avatar.jpeg')
    }

    # 保存消息
    messages_ref.child(message_id).set(message_data)

    # 更新最后一条消息到好友关系中
    friendships_ref = db.reference('friendships')
    friendships_ref.child(sender_email_prefix).child(receiver_id).update({
        'last_message': content,
        'last_message_time': current_time
    })
    friendships_ref.child(receiver_id).child(sender_email_prefix).update({
        'last_message': content,
        'last_message_time': current_time
    })

    return jsonify({
        'status': 'success',
        'message': message_data
    })


@app.route('/donation')
def donation():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取用户数据
    user_data = db.reference(f"users/{email_prefix}").get()

    # 获取用户奖励
    user_rewards = db.reference(f"user_rewards/{email_prefix}").get()

    return render_template('donation.html', user_info=user_info, user_data=user_data, user_rewards=user_rewards)


@app.route('/make_donation', methods=['POST'])
def make_donation():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "用户未登录"}), 403

    data = request.get_json()
    amount = float(data.get('amount', 0))
    donation_type = data.get('type')
    activity_id = data.get('activity_id')

    if amount <= 0:
        return jsonify({"status": "error", "message": "The donation amount must be greater than 0"}), 400

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    user_ref = db.reference(f'users/{email_prefix}')
    user_data = user_ref.get()

    # 生成唯一的捐赠ID
    donation_id = str(uuid.uuid4())

    # 创建捐赠记录
    donation_data = {
        'id': donation_id,
        'user_id': email_prefix,
        'amount': amount,
        'type': donation_type,
        'timestamp': datetime.now().isoformat(),
        'status': 'completed'
    }

    if activity_id:
        donation_data['activity_id'] = activity_id
        # 更新活动的募集金额
        activity_ref = db.reference(f'activities/{activity_id}')
        activity_data = activity_ref.get()
        if activity_data:
            current_amount = float(activity_data.get('current_amount', 0))
            activity_ref.update({
                'current_amount': current_amount + amount
            })

    # 更新用户的捐赠统计
    donation_count = user_data.get('donation_count', 0) + 1
    total_donation = user_data.get('total_donation', 0) + amount

    user_ref.update({
        'donation_count': donation_count,
        'total_donation': total_donation
    })

    # 存储捐赠记录
    db.reference(f'donations/{donation_id}').set(donation_data)

    # 计算奖励
    rewards = calculate_rewards(donation_count, amount)

    # 如果是首次捐赠，生成证书
    if donation_count == 1:
        certificate_code = generate_donation_certificate(email_prefix, user_data.get('name', 'Compassionate User'))
        rewards.append({
            'type': 'certificate',
            'name': 'First Donation Certificate',
            'description': 'Thank you for your first donation!',
            'access_code': certificate_code
        })

    # 如果是活动捐赠，处理活动特定奖励
    if activity_id:
        activity_rewards = process_activity_rewards(activity_id, amount)
        rewards.extend(activity_rewards)

    # 在Firebase中更新用户奖励
    rewards_ref = db.reference(f'user_rewards/{email_prefix}')

    for reward in rewards:
        if reward.get('type') in ['badge', 'hat', 'tshirt', 'backpack', 'trophy']:
            reward_type = reward.get('type')
            reward_data = rewards_ref.child(reward_type).get() or {}

            current_count = reward_data.get('count', 0) + 1
            rewards_ref.child(reward_type).update({
                'reward_type': reward_type,
                'reward_name': reward.get('name', reward_type),
                'count': current_count,
                'last_updated': datetime.now().isoformat()
            })

    return jsonify({
        "status": "success",
        "rewards": rewards
    }), 200


def calculate_rewards(donation_count, amount):
    rewards = []

    # 首次捐赠奖励
    if donation_count == 1:
        rewards.append({
            'type': 'certificate',
            'name': '首次捐赠证书',
            'description': '感谢您的首次捐赠！这是我们为您准备的特别证书。'
        })

    # 勋章奖励 - 只在特定次数时才播报获得
    medal_thresholds = [5, 10, 20, 50, 100]
    if donation_count in medal_thresholds:
        medal_level = get_medal_level(donation_count)
        rewards.append({
            'type': 'medal',
            'name': medal_level,
            'description': f'Congratulations on reaching {medal_level} level!'
        })

    # 根据金额获得的礼物
    if amount >= 100:
        badge_count = math.floor(amount / 100)
        for i in range(badge_count):
            rewards.append({
                'type': 'badge',
                'name': 'Heart Badge',
                'description': 'Thank you for your donation! This is a symbol of love.'
            })

    if amount >= 500:
        hat_count = math.floor(amount / 500)
        for i in range(hat_count):
            rewards.append({
                'type': 'hat',
                'name': 'Guardian Hat',
                'description': 'This exquisite hat is a symbol of your commitment to animal protection.'
            })

    if amount >= 1000:
        tshirt_count = math.floor(amount / 1000)
        for i in range(tshirt_count):
            rewards.append({
                'type': 'tshirt',
                'name': 'Guardian T-Shirt',
                'description': 'Wear this T-shirt to show your support for animal protection!'
            })

    if amount >= 5000:
        backpack_count = math.floor(amount / 5000)
        for i in range(backpack_count):
            rewards.append({
                'type': 'backpack',
                'name': 'Limited Edition Backpack',
                'description': 'This limited edition backpack is a symbol of your generous donation.'
            })

    if amount >= 10000:
        trophy_count = math.floor(amount / 10000)
        for i in range(trophy_count):
            rewards.append({
                'type': 'trophy',
                'name': 'Annual Guardian Trophy',
                'description': 'This trophy represents your significant contribution to wildlife conservation!'
            })

    return rewards


def generate_access_code():
    """生成8位随机兑换码"""
    return ''.join(random.choices('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', k=8))


def generate_donation_certificate(user_id, username):
    """生成捐赠证书编号"""
    certificate_id = f"CERT-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8]}"

    # 存储证书信息
    db.reference(f'certificates/{certificate_id}').set({
        'user_id': user_id,
        'username': username,
        'issue_date': datetime.now().isoformat(),
        'type': 'donation_first'
    })

    return certificate_id


def get_activity_data(activity_id):
    """获取活动数据"""
    activity_ref = db.reference(f'activities/{activity_id}')
    return activity_ref.get()


def process_activity_rewards(activity_id, amount):
    """处理活动特定的奖励"""
    activity_data = get_activity_data(activity_id)
    if not activity_data:
        return []

    rewards = []
    reward_rules = activity_data.get('reward_rules', {})

    for level, reward in reward_rules.items():
        if amount >= float(level):
            rewards.append({
                'type': reward.get('type', 'activity_reward'),
                'name': reward.get('name', 'Activity rewards'),
                'description': reward.get('description', 'Thank you for participating in the event'),
                'access_code': generate_access_code()
            })

    return rewards


# 添加获取用户奖励的API - 使用Firebase
@app.route('/get_user_rewards')
def get_user_rewards():
    # 检查是否已登录
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': 'User has not login'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 从Firebase获取用户奖励
    rewards_ref = db.reference(f'user_rewards/{email_prefix}')
    rewards_data = rewards_ref.get() or {}

    rewards = []
    for reward_type, reward_info in rewards_data.items():
        if isinstance(reward_info, dict):  # 确保数据格式正确
            rewards.append({
                'reward_type': reward_info.get('reward_type', reward_type),
                'reward_name': reward_info.get('reward_name', reward_type),
                'count': reward_info.get('count', 0)
            })

    return jsonify({
        'status': 'success',
        'rewards': rewards
    })


@app.route('/get_activities')
def get_activities():
    """Get all ongoing public welfare activities"""
    activities_ref = db.reference('activities')
    activities = activities_ref.get()

    if not activities:
        # If there are no activity data, initialize some sample activities
        sample_activities = {
            'activity1': {
                'id': 'activity1',
                'title': 'Protecting Giant Panda Habitats',
                'description': 'Support the conservation of giant panda habitats in Sichuan and help maintain the bamboo forest ecosystem.',
                'image_url': '/static/images/activities/panda.jpg',
                'organizer': 'China Wildlife Conservation Association',
                'location': 'Sichuan Province',
                'start_date': '2024-03-01',
                'end_date': '2024-12-31',
                'target_amount': 100000,
                'current_amount': 45000,
                'min_donation': 10,
                'reward_rules': {
                    '100': {
                        'type': 'tracking',
                        'name': 'Panda Tracking Access',
                        'description': 'Gain access to view daily activities of the giant pandas.'
                    },
                    '500': {
                        'type': 'updates',
                        'name': 'Monthly Report',
                        'description': 'Gain access to view monthly progress report of the project.'
                    },
                    '1000': {
                        'type': 'adoption',
                        'name': 'Panda Adoption Certificate',
                        'description': 'Receive a one-year qualification for panda adoption.'
                    }
                }
            },
            'activity2': {
                'id': 'activity2',
                'title': 'Snow Leopard Conservation Program',
                'description': 'Support the conservation of snow leopards and their habitats on the Tibetan Plateau.',
                'image_url': '/static/images/activities/snowleopard.jpg',
                'organizer': 'Mountain Species Conservation Alliance',
                'location': 'Qinghai Province',
                'start_date': '2024-03-15',
                'end_date': '2024-12-31',
                'target_amount': 80000,
                'current_amount': 25000,
                'min_donation': 10,
                'reward_rules': {
                    '100': {
                        'type': 'tracking',
                        'name': 'Snow Leopard Monitoring Access',
                        'description': 'Gain access to view footage from infrared cameras monitoring snow leopards.'
                    },
                    '500': {
                        'type': 'updates',
                        'name': 'Quarterly Report',
                        'description': 'Gain access to view quarterly progress report of the project.'
                    },
                    '1000': {
                        'type': 'adoption',
                        'name': 'Snow Leopard Adoption Certificate',
                        'description': 'Receive a one-year qualification for snow leopard adoption.'
                    }
                }
            },
            'activity3': {
                'id': 'activity3',
                'title': 'Sea Turtle Rescue Station Construction',
                'description': 'Support the construction and operation of the sea turtle rescue station in Hainan to protect endangered sea turtles.',
                'image_url': '/static/images/activities/turtle.jpg',
                'organizer': 'Marine Life Conservation Association',
                'location': 'Hainan Province',
                'start_date': '2024-03-20',
                'end_date': '2024-12-31',
                'target_amount': 50000,
                'current_amount': 15000,
                'min_donation': 10,
                'reward_rules': {
                    '100': {
                        'type': 'tracking',
                        'name': 'Rescue Station Live Stream',
                        'description': 'Gain access to view the live stream from the rescue station.'
                    },
                    '500': {
                        'type': 'updates',
                        'name': 'Rescue Log',
                        'description': 'Gain access to view work log of the rescue station.'
                    },
                    '1000': {
                        'type': 'adoption',
                        'name': 'Sea Turtle Adoption Certificate',
                        'description': 'Receive a one-year qualification for sea turtle adoption.'
                    }
                }
            }
        }
        activities_ref.set(sample_activities)
        activities = sample_activities

    return jsonify({
        "status": "success",
        "activities": activities
    }), 200


@app.route('/get_user_donations')
def get_user_donations():
    if 'user' not in session:
        return jsonify({"status": "error", "message": "user has not login"}), 403

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取用户的捐赠记录
    donations_ref = db.reference('donations')
    user_donations = []

    # 查询所有捐赠记录
    all_donations = donations_ref.get()
    if all_donations:
        for donation_id, donation in all_donations.items():
            if donation.get('user_id') == email_prefix:
                # 如果是活动捐赠，获取活动信息
                if donation.get('activity_id'):
                    activity_ref = db.reference(f'activities/{donation["activity_id"]}')
                    activity_data = activity_ref.get()
                    if activity_data:
                        donation['activity'] = {
                            'title': activity_data.get('title'),
                            'image_url': activity_data.get('image_url')
                        }
                user_donations.append(donation)

    # 按时间倒序排序
    user_donations.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

    return jsonify({
        "status": "success",
        "donations": user_donations
    }), 200


# 在文件开头的导入部分下方添加
def get_medal_level(count):
    if count >= 100:
        return "Diamond Guardian"
    elif count >= 50:
        return "Platinum Guardian"
    elif count >= 20:
        return "Gold Guardian"
    elif count >= 10:
        return "Silver Guardian"
    elif count >= 5:
        return "Bronze Guardian"
    return "No Medals Yet"


# 在创建 app 后添加
app.jinja_env.globals.update(get_medal_level=get_medal_level)


# 添加创建活动的路由
@app.route('/create_activity', methods=['POST'])
def create_activity():
    """处理创建新公益活动的请求"""
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': 'Please login first'}), 403

    try:
        # 获取表单数据
        title = request.form.get('title')
        organizer = request.form.get('organizer')
        description = request.form.get('description')
        location = request.form.get('location')
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        target_amount = float(request.form.get('target_amount', 0))
        min_donation = float(request.form.get('min_donation', 0))
        current_amount = float(request.form.get('current_amount', 0))

        # 获取奖励规则JSON数据
        reward_rules_json = request.form.get('reward_rules', '{}')
        try:
            reward_rules = json.loads(reward_rules_json)
            print(f"Received reward rules:{reward_rules}")
        except json.JSONDecodeError as e:
            print(f"Error parsing reward rules: {str(e)}")
            reward_rules = {}

        # 验证必要字段
        if not all([title, organizer, description, location, start_date, end_date]):
            return jsonify({'status': 'error', 'message': 'Please fill in all required information'}), 400

        # 验证金额
        if target_amount <= 0 or min_donation <= 0:
            return jsonify({'status': 'error',
                            'message': 'The target amount and minimum donation amount must be greater than 0'}), 400

        if start_date >= end_date:
            return jsonify({'status': 'error', 'message': 'The end time must be later than the start time'}), 400

        # 检查是否有图片上传
        if 'image' not in request.files:
            return jsonify({'status': 'error', 'message': 'No image uploaded'}), 400

        image_file = request.files['image']

        # 检查文件是否有效
        if image_file.filename == '':
            return jsonify({'status': 'error', 'message': 'No image selected'}), 400

        # 检查文件类型
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
        if '.' not in image_file.filename or \
                image_file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
            return jsonify({'status': 'error', 'message': 'Unsupported image format'}), 400

        # 生成唯一文件名
        unique_filename = f"{uuid.uuid4()}.{image_file.filename.rsplit('.', 1)[1].lower()}"

        # 保存图片到Firebase Storage
        # 注意：这里假设您已经设置了Firebase Storage
        # 如果您使用的是本地文件存储，请调整此部分代码
        upload_folder = os.path.join('static', 'images', 'activities')
        os.makedirs(upload_folder, exist_ok=True)

        image_path = os.path.join(upload_folder, unique_filename)
        image_file.save(image_path)

        # 图片URL
        image_url = f"/static/images/activities/{unique_filename}"

        # 生成活动ID
        activity_id = str(uuid.uuid4())

        # 创建时间戳
        timestamp = int(time.time() * 1000)  # Firebase使用毫秒时间戳

        # 使用Firebase API保存活动信息
        user_info = session['user']
        email_prefix = user_info['email'].split('@')[0]

        activity_data = {
            'id': activity_id,
            'title': title,
            'organizer': organizer,
            'description': description,
            'location': location,
            'start_date': start_date,
            'end_date': end_date,
            'target_amount': target_amount,
            'min_donation': min_donation,
            'current_amount': current_amount,
            'image_url': image_url,
            'creator_id': email_prefix,
            'timestamp': timestamp,
            'reward_rules': reward_rules  # 添加奖励规则到活动数据
        }

        # 保存到Firebase
        activities_ref = db.reference('activities')
        activities_ref.child(activity_id).set(activity_data)

        return jsonify({
            'status': 'success',
            'message': 'Activity created successfully',
            'activity': activity_data
        }), 200

    except Exception as e:
        print(f"Create activity error:{str(e)}")
        return jsonify({'status': 'error', 'message': f'Server error:{str(e)}'}), 500


@app.route('/save_certificate', methods=['POST'])
def save_certificate():
    if 'email' not in session:
        return jsonify({'status': 'error', 'message': 'User must be logged in to save certificate'})

    try:
        data = request.json

        # 获取用户的keyID（邮箱前缀）
        email = session['email']
        key_id = email.split('@')[0]

        # 添加更新时间戳
        data['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 保存证书数据到数据库
        certificates_ref = db.reference(f'user_certificates/{key_id}')
        certificates_ref.set(data)

        return jsonify({'status': 'success', 'message': 'Certificate saved successfully'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/get_user_certificate', methods=['GET'])
def get_user_certificate():
    if 'email' not in session:
        return jsonify({'status': 'error', 'message': 'User must be logged in to get certificate'})

    try:
        # 获取用户的keyID（邮箱前缀）
        email = session['email']
        key_id = email.split('@')[0]

        # 从数据库获取证书数据
        certificates_ref = db.reference(f'user_certificates/{key_id}')
        certificate_data = certificates_ref.get()

        if certificate_data:
            return jsonify({'status': 'success', 'certificate': certificate_data})
        else:
            return jsonify({'status': 'error', 'message': 'No certificate found for user'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/my_panda')
def my_panda():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    user_ref = db.reference(f"users/{email_prefix}")
    user_data = user_ref.get() or {}
    user_score = user_data.get('score', 0)

    # 检查Firebase中是否存在该用户的小熊猫数据
    ref = db.reference('my_panda')
    panda_data = ref.child(email_prefix).get()

    # 添加省份地图数据
    province_data = {
        '上海': url_for('static', filename='json/data/data-1482909900836-H1BC_1WHg.json'),
        '河北': url_for('static', filename='json/data/data-1482909799572-Hkgu_yWSg.json'),
        '山西': url_for('static', filename='json/data/data-1482909909703-SyCA_JbSg.json'),
        '内蒙古': url_for('static', filename='json/data/data-1482909841923-rkqqdyZSe.json'),
        '辽宁': url_for('static', filename='json/data/data-1482909836074-rJV9O1-Hg.json'),
        '吉林': url_for('static', filename='json/data/data-1482909832739-rJ-cdy-Hx.json'),
        '黑龙江': url_for('static', filename='json/data/data-1482909803892-Hy4__J-Sx.json'),
        '江苏': url_for('static', filename='json/data/data-1482909823260-HkDtOJZBx.json'),
        '浙江': url_for('static', filename='json/data/data-1482909960637-rkZMYkZBx.json'),
        '安徽': url_for('static', filename='json/data/data-1482909768458-HJlU_yWBe.json'),
        '福建': url_for('static', filename='json/data/data-1478782908884-B1H6yezWe.json'),
        '江西': url_for('static', filename='json/data/data-1482909827542-r12YOJWHe.json'),
        '山东': url_for('static', filename='json/data/data-1482909892121-BJ3auk-Se.json'),
        '河南': url_for('static', filename='json/data/data-1482909807135-SJPudkWre.json'),
        '湖北': url_for('static', filename='json/data/data-1482909813213-Hy6u_kbrl.json'),
        '湖南': url_for('static', filename='json/data/data-1482909818685-H17FOkZSl.json'),
        '广东': url_for('static', filename='json/data/data-1482909784051-BJgwuy-Sl.json'),
        '广西': url_for('static', filename='json/data/data-1482909787648-SyEPuJbSg.json'),
        '海南': url_for('static', filename='json/data/data-1482909796480-H12P_J-Bg.json'),
        '四川': url_for('static', filename='json/data/data-1482909931094-H17eKk-rg.json'),
        '贵州': url_for('static', filename='json/data/data-1482909791334-Bkwvd1bBe.json'),
        '云南': url_for('static', filename='json/data/data-1482909957601-HkA-FyWSx.json'),
        '西藏': url_for('static', filename='json/data/data-1482927407942-SkOV6Qbrl.json'),
        '陕西': url_for('static', filename='json/data/data-1482909918961-BJw1FyZHg.json'),
        '甘肃': url_for('static', filename='json/data/data-1482909780863-r1aIdyWHl.json'),
        '青海': url_for('static', filename='json/data/data-1482909853618-B1IiOyZSl.json'),
        '宁夏': url_for('static', filename='json/data/data-1482909848690-HJWiuy-Bg.json'),
        '新疆': url_for('static', filename='json/data/data-1482909952731-B1YZKkbBx.json'),
        '北京': url_for('static', filename='json/data/data-1482818963027-Hko9SKJrg.json'),
        '天津': url_for('static', filename='json/data/data-1482909944620-r1-WKyWHg.json'),
        '重庆': url_for('static', filename='json/data/data-1482909775470-HJDIdk-Se.json'),
        '香港': url_for('static', filename='json/data/data-1461584707906-r1hSmtsx.json'),
        '澳门': url_for('static', filename='json/data/data-1482909771696-ByVIdJWBx.json')
    }
    china_map_url = url_for('static', filename='json/data/data-1527045631990-r1dZ0IM1X.json')

    return render_template('my_panda.html',
                           has_panda=bool(panda_data),
                           score=user_score,
                           provinces=province_data,
                           china_map_url=china_map_url)


@app.route('/adopt_panda', methods=['POST'])
def adopt_panda():
    if 'user' not in session:
        return jsonify({'success': False, 'message': '请先登录'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取熊猫名字
    data = request.get_json()
    panda_name = data.get('name', '').strip()

    if not panda_name:
        return jsonify({'success': False, 'message': 'Please enter the name of the red panda'})

    # 获取用户当前积分
    user_ref = db.reference(f"users/{email_prefix}")
    user_data = user_ref.get()
    user_score = user_data.get('score', 0)

    # 检查积分是否足够
    if user_score < 50:
        return jsonify({'success': False, 'message': 'If the points are insufficient, 50 points are required for adoption'})

    # 检查是否已经认养
    panda_ref = db.reference('my_panda')
    existing_panda = panda_ref.child(email_prefix).get()
    if existing_panda:
        return jsonify({'success': False, 'message': 'You have already adopted a red panda'})

    try:
        # 扣除积分
        new_score = user_score - 50
        user_ref.update({'score': new_score})

        # 创建小熊猫记录，添加饱食度和心情值以及疲劳值
        panda_data = {
            'name': panda_name,
            'adopted_at': datetime.now().isoformat(),
            'user_id': email_prefix,
            'fullness': 100,  # 初始饱食度100%
            'mood': 100,  # 初始心情100%
            'fatigue': 0,  # 初始疲劳值0%
            'last_updated': datetime.now().isoformat()  # 最后更新时间
        }
        panda_ref.child(email_prefix).set(panda_data)

        return jsonify({
            'success': True,
            'message': f'Congratulations on successfully adopting the red panda {panda_name}！'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Adoption failed. Please try again: {str(e)}'})

@app.route('/update_panda_status', methods=['POST'])
def update_panda_status():
    """更新小熊猫的饱食度、心情值和疲劳值"""
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Please log in first'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取小熊猫数据
    panda_ref = db.reference(f'my_panda/{email_prefix}')
    panda_data = panda_ref.get()

    if not panda_data:
        return jsonify({'success': False, 'message': 'You have not adopted a red panda yet'})

    # 计算经过的时间
    last_updated = datetime.fromisoformat(panda_data.get('last_updated'))
    current_time = datetime.now()
    time_diff = (current_time - last_updated).total_seconds()  # 转换为秒

    # 计算新的状态值，每10秒钟减少1%
    decrease_rate = 1  # 每10秒减少1%
    time_units = time_diff // 10  # 计算经过了多少个10秒
    decrease_amount = decrease_rate * time_units

    fullness = max(0, panda_data.get('fullness', 100) - decrease_amount)
    mood = max(0, panda_data.get('mood', 100) - decrease_amount)
    fatigue = max(0, panda_data.get('fatigue', 0) - decrease_amount)

    # 更新数据
    panda_ref.update({
        'fullness': fullness,
        'mood': mood,
        'fatigue': fatigue,
        'last_updated': current_time.isoformat()
    })

    # 返回更新后的状态
    return jsonify({
        'success': True,
        'fullness': fullness,
        'mood': mood,
        'fatigue': fatigue,
        'needSadFace': fullness < 50 or mood < 50 or fatigue >= 80
    })

@app.route('/feed_panda', methods=['POST'])
def feed_panda():
    """喂食小熊猫，根据食物类型增加不同的饱食度"""
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Please log in first'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取小熊猫数据
    panda_ref = db.reference(f'my_panda/{email_prefix}')
    panda_data = panda_ref.get()

    if not panda_data:
        return jsonify({'success': False, 'message': 'You have not adopted a red panda yet'})

    # 获取用户数据，检查积分是否足够
    user_ref = db.reference(f'users/{email_prefix}')
    user_data = user_ref.get()

    if not user_data:
        return jsonify({'success': False, 'message': 'Failed to obtain user data'})

    current_score = user_data.get('score', 0)

    # 喂食需要5分积分
    if current_score < 5:
        return jsonify({'success': False, 'message': 'Insufficient points. Feeding requires 5 points'})

    # 获取食物类型
    data = request.get_json()
    food_type = data.get('foodType', 'bamboo')

    # 根据食物类型决定饱食度增加值
    fullness_increase = 0
    message = ""

    if food_type == 'bamboo' or food_type == 'apple':
        fullness_increase = 15
        message = "Bamboo and apples are your panda's favorites! (+15 fullness)"
    elif food_type == 'meat':
        fullness_increase = 5
        message = "Your panda doesn't really like meat... (+5 fullness)"
    elif food_type == 'onion':
        fullness_increase = 0
        message = "Your panda is allergic to onions and feels unhappy... (no fullness gained)"
    else:
        fullness_increase = 10
        message = "Feeding successful! (+10 fullness)"

    # 更新饱食度，最大为100
    current_fullness = panda_data.get('fullness', 0)
    new_fullness = min(100, current_fullness + fullness_increase)

    # 更新熊猫数据
    panda_ref.update({
        'fullness': new_fullness,
        'last_updated': datetime.now().isoformat()
    })

    # 扣除用户积分
    new_score = current_score - 5
    user_ref.update({
        'score': new_score
    })

    return jsonify({
        'success': True,
        'fullness': new_fullness,
        'score': new_score,
        'message': message
    })

@app.route('/play_with_panda', methods=['POST'])
def play_with_panda():
    """与小熊猫玩耍，增加心情值"""
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Please log in first'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取小熊猫数据
    panda_ref = db.reference(f'my_panda/{email_prefix}')
    panda_data = panda_ref.get()

    if not panda_data:
        return jsonify({'success': False, 'message': 'You have not adopted a red panda yet'})

    # 更新心情值，最大为100
    current_mood = panda_data.get('mood', 0)
    new_mood = min(100, current_mood + 20)  # 每次玩耍增加20%心情

    # 更新数据
    panda_ref.update({
        'mood': new_mood,
        'last_updated': datetime.now().isoformat()
    })

    return jsonify({
        'success': True,
        'mood': new_mood,
        'message': 'Your panda is very happy now! (+20 mood)'
    })


@app.route('/save_fatigue', methods=['POST'])
def save_fatigue():
    """保存疲劳值"""
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Please log in first'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取请求数据
    data = request.get_json()
    fatigue = data.get('fatigue', 0)

    # 获取小熊猫数据
    panda_ref = db.reference(f'my_panda/{email_prefix}')
    panda_data = panda_ref.get()

    if not panda_data:
        return jsonify({'success': False, 'message': 'You have not adopted a red panda yet'})

    # 更新疲劳值
    panda_ref.update({
        'fatigue': fatigue
    })

    return jsonify({
        'success': True,
        'fatigue': fatigue,
        'message': 'The fatigue value has been updated'
    })


@app.route('/travel_to_province', methods=['POST'])
def travel_to_province():
    # 检查用户是否登录
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Please log in first'})

    # 获取请求数据
    data = request.get_json()
    province = data.get('province')
    
    # 获取用户信息
    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    
    # 获取用户积分 - 优先使用请求中的积分，否则从数据库获取
    user_score = data.get('user_score')
    if user_score is None:
        # 如果请求中没有积分信息，从数据库获取
        user_ref = db.reference(f'users/{email_prefix}')
        user_data = user_ref.get() or {}
        user_score = user_data.get('score', 0)
    else:
        # 确保user_score是整数
        user_score = int(user_score)

    if not province:
        return jsonify({'success': False, 'message': 'Please select a province'})

    # 检查积分是否足够
    if user_score < 10:
        return jsonify({'success': False, 'message': 'The points are insufficient. Ten points are needed for the trip'})

    # 获取小熊猫数据
    panda_ref = db.reference(f'my_panda/{email_prefix}')
    panda_data = panda_ref.get()

    if not panda_data:
        return jsonify({'success': False, 'message': 'You have not adopted a red panda yet'})

    # 检查省份是否已经去过
    traveled_provinces = panda_data.get('traveled_provinces', [])
    
    if province in traveled_provinces:
        return jsonify({'success': False, 'message': f'Your red panda has been to {province}'})

    # 扣除积分
    user_ref = db.reference(f'users/{email_prefix}')
    new_score = user_score - 10
    user_ref.update({'score': new_score})

    # 更新旅行记录和身体状态
    # 添加到已去过的省份列表
    traveled_provinces.append(province)
    
    # 计算饱食度变化 (旅行会减少15-25点饱食度)
    fullness_reduction = random.randint(15, 25)
    current_fullness = panda_data.get('fullness', 100)
    new_fullness = max(0, current_fullness - fullness_reduction)

    # 更新小熊猫数据
    panda_ref.update({
        'traveled_provinces': traveled_provinces,
        'fullness': new_fullness,
        'last_updated': datetime.now().isoformat()
    })

    # 获取当地照片URL (省略部分旅行照片处理逻辑)
    province_photo_url = f'/static/images/provinces/{province}.jpg'
    
    # 获取省份推荐指数和理由数据
    province_recommendation = {
        "北京": { "stars": "★", "reason": "Super urban jungle with hardly any panda-friendly green corners." },
        "天津": { "stars": "★", "reason": "Too busy, too built-up — pandas would have to commute far for bamboo snacks." },
        "河北": { "stars": "★★", "reason": "Some hills play hide-and-seek with forests, but pandas still feel the city buzz." },
        "山西": { "stars": "★★", "reason": "Hills try their best, but the air's a bit too dry for panda spa days." },
        "内蒙古": { "stars": "★", "reason": "Grasslands galore, but for pandas, it's more tumbleweed than bamboo breeze." },
        "辽宁": { "stars": "★", "reason": "Industry overload, nature's hiding — pandas miss the leafy luxury." },
        "吉林": { "stars": "★", "reason": "Chilly winters and pine forests, not the panda's warm bamboo dream." },
        "黑龙江": { "stars": "★", "reason": "Freezing temps and lots of needles (pine trees), but no soft panda beds." },
        "上海": { "stars": "★", "reason": "All city, no forest — pandas are totally lost here." },
        "江苏": { "stars": "★★", "reason": "Water's nice, but panda pals prefer more forests, less concrete." },
        "浙江": { "stars": "★★", "reason": "Warm and wet, but mostly flat and coastal — pandas crave more bamboo hills." },
        "安徽": { "stars": "★★★", "reason": "Forests say hello! But the cities still crash the panda party." },
        "福建": { "stars": "★★", "reason": "Warm and cozy, yet pandas wish for taller trees and denser bamboo." },
        "江西": { "stars": "★★★", "reason": "Hills and forests invite pandas to play hide-and-seek all day!" },
        "山东": { "stars": "★", "reason": "Lots of cities and plains — pandas snooze on the concrete beds here." },
        "河南": { "stars": "★", "reason": "Flat lands with busy folks, panda naps come second." },
        "湖北": { "stars": "★★", "reason": "Some woods, but too much hustle for peaceful panda strolls." },
        "湖南": { "stars": "★★★", "reason": "Cozy hills and moist air — panda paradise in the making!" },
        "广东": { "stars": "★★", "reason": "Warm and wet, but pandas wish for higher bamboo towers." },
        "广西": { "stars": "★★★", "reason": "Mountains and magic karst make this a cool panda playground." },
        "海南": { "stars": "☆", "reason": "Tropics are fun but way too hot for shy bamboo-eating bears." },
        "重庆": { "stars": "★★★★", "reason": "Misty mountains and wet air — pandas' dream resort!" },
        "四川": { "stars": "★★★★★", "reason": "Panda central! Ultimate bamboo buffet and cozy forest naps." },
        "贵州": { "stars": "★★★★", "reason": "Rich forests invite pandas to explore and chill all day." },
        "云南": { "stars": "★★★★★", "reason": "A jungle wonderland where pandas feel right at home." },
        "西藏": { "stars": "★★", "reason": "High altitude antics — only some edges suit these bamboo lovers." },
        "青海": { "stars": "★", "reason": "Cold and thin air — pandas prefer fluffier forests." },
        "宁夏": { "stars": "☆", "reason": "Dry and sandy — pandas wave goodbye to this arid playground." },
        "新疆": { "stars": "☆", "reason": "Deserts and mountains are cool, but not panda cool." },
        "甘肃": { "stars": "★", "reason": "Dry lands with few forests — pandas find fewer bamboo treats here." },
        "香港": { "stars": "★", "reason": "Too much city sparkle, too little panda nature sparkle." },
        "澳门": { "stars": "★", "reason": "All lights, no leaves — panda naps not included." },
        "台湾": { "stars": "★★★★", "reason": "Mountains and moist air make for a cozy panda hideout!"}
    }
    
    province_data = province_recommendation.get(province, {"stars": "★★★", "reason": "未知区域评价"})
    
    # 同步更新user_travels数据库
    try:
        # 获取现有的访问记录
        travels_ref = db.reference(f'user_travels/{email_prefix}')
        travels_data = travels_ref.get() or {}
        
        # 添加新的省份访问记录
        travels_data[province] = {
            'visitedAt': datetime.now().isoformat(),
            'stars': province_data["stars"],
            'reason': province_data["reason"]
        }
        
        # 保存回数据库
        travels_ref.set(travels_data)
    except Exception as e:
        app.logger.error(f"更新user_travels记录出错: {str(e)}")
        # 继续执行，不影响主流程
    
    # 返回成功消息和新的状态
    return jsonify({
        'success': True,
        'message': f'Your red panda has successfully traveled to {province}!',
        'province': province,
        'photo': province_photo_url,
        'fullness': new_fullness,
        'traveled_provinces': traveled_provinces,
        'score': new_score  # 返回新的积分值给前端
    })


@app.route('/friend_requests')
def friend_requests_page():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # 获取好友请求
    friend_requests_ref = db.reference(f'friend_requests/{email_prefix}')
    friend_requests = friend_requests_ref.get() or {}

    # 转换为列表格式
    requests_list = []
    for sender_id, request_data in friend_requests.items():
        if request_data.get('status') == 'pending':
            # 处理时间戳
            timestamp = request_data.get('timestamp')
            if isinstance(timestamp, (int, float)):
                # 如果是数字类型的时间戳，转换为ISO格式字符串
                timestamp = datetime.fromtimestamp(timestamp / 1000).isoformat()
            elif not timestamp:
                timestamp = datetime.now().isoformat()

            requests_list.append({
                'user_id': sender_id,
                'username': request_data.get('sender_name'),
                'avatar': request_data.get('sender_avatar', '/static/images/default-avatar.jpeg'),
                'timestamp': timestamp
            })

    # 按时间排序
    requests_list.sort(key=lambda x: x['timestamp'] if x['timestamp'] else '', reverse=True)

    return render_template('friend_requests.html', requests=requests_list)


@app.route('/search_friends')
def search_friends():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('search_friends.html')


@app.route('/buy_fatigue_potion', methods=['POST'])
def buy_fatigue_potion():
    # 检查用户是否登录
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'User must be logged in to buy potions'}), 401

    # 获取请求数据
    data = request.get_json()
    user_id = data.get('user_id')

    # 验证用户ID
    if not user_id:
        return jsonify({'success': False, 'message': 'Invalid user ID'}), 400

    # 当前时间戳
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        # 获取用户数据
        user_ref = db.reference(f'users/{user_id}')
        user_data = user_ref.get()

        if not user_data:
            return jsonify({'success': False, 'message': 'User not found'}), 404

        # 检查用户积分是否足够
        current_score = user_data.get('score', 0)
        if current_score < 20:
            return jsonify({'success': False, 'message': 'Not enough points. You need 20 points to buy a potion.'}), 400

        # 获取熊猫数据
        panda_ref = db.reference(f'my_panda/{user_id}')
        panda_data = panda_ref.get()

        if not panda_data:
            return jsonify({'success': False, 'message': 'No panda found for this user'}), 404

        # 获取当前疲劳值
        current_fatigue = panda_data.get('fatigue', 0)

        # 如果疲劳值为0，不需要使用药水
        if current_fatigue == 0:
            return jsonify({'success': False, 'message': 'Your panda is not tired! No need to use potion.'}), 400

        # 计算新的疲劳值（减少50点，最低为0）
        new_fatigue = max(0, current_fatigue - 50)

        # 更新熊猫疲劳值
        panda_ref.update({
            'fatigue': new_fatigue,
            'last_updated': current_time
        })

        # 计算新的积分
        new_score = current_score - 20

        # 更新用户积分
        user_ref.update({
            'score': new_score
        })

        # 记录药水使用历史
        history_ref = db.reference(f'potion_history/{user_id}')
        history_entry = {
            'timestamp': current_time,
            'fatigue_before': current_fatigue,
            'fatigue_after': new_fatigue,
            'cost': 20
        }

        # 生成唯一的历史记录ID
        history_id = f"potion_{int(time.time())}"
        history_ref.child(history_id).set(history_entry)

        # 返回成功响应
        return jsonify({
            'success': True,
            'message': 'Fatigue relief potion used successfully!',
            'new_fatigue': new_fatigue,
            'new_score': new_score,
            'timestamp': current_time
        })

    except Exception as e:
        # 记录错误并返回错误响应
        print(f"Error buying fatigue potion: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


@app.route('/manage_posts')
def manage_posts():
    if 'user' not in session:
        return redirect(url_for('login'))

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    # Get user data
    user_ref = db.reference(f'users/{email_prefix}')
    user_data = user_ref.get()

    if not user_data:
        return redirect(url_for('login'))

    return render_template('manage_posts.html', user_info=user_info, user_data=user_data)


@app.route('/get_user_posts')
def get_user_posts():
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    try:
        # Get all posts from Firebase
        posts_ref = db.reference('posts')
        all_posts = posts_ref.get() or {}

        # Filter posts by user's keyId
        user_posts = []
        for post_id, post in all_posts.items():
            if post.get('keyid') == email_prefix:
                # Add post_id to the post data
                post['post_id'] = post_id

                # Handle media URLs
                media = post.get('media', [])
                if isinstance(media, str):
                    # If media is a string, convert it to a list with one item
                    media = [media]
                elif isinstance(media, dict):
                    # If media is a dictionary, extract the values
                    media = list(media.values())
                elif media is None:
                    media = []

                # Ensure all media URLs are properly formatted
                formatted_media = []
                for m in media:
                    if m and isinstance(m, str):
                        if not m.startswith('/'):
                            m = f"/static/images/post/{m}"
                        formatted_media.append(m)

                post['media'] = formatted_media

                # Ensure other fields have default values
                post['title'] = post.get('title', '')
                post['content'] = post.get('content', '')
                post['like'] = post.get('like', 0)
                post['timestamp'] = post.get('timestamp', '')

                # Get user info
                user_ref = db.reference(f'users/{email_prefix}')
                user_data = user_ref.get()
                if user_data:
                    post['username'] = user_data.get('name', email_prefix)
                    post['avatar'] = user_data.get('avatar', '/static/images/default-avatar.jpeg')

                # Get comments
                comments_ref = db.reference(f'comments/{post_id}').get()
                comments = []
                if comments_ref:
                    comment_items = []
                    if isinstance(comments_ref, list):
                        comment_items = [c for c in comments_ref if c]
                    elif isinstance(comments_ref, dict):
                        comment_items = list(comments_ref.values())

                    for comment in comment_items:
                        if not isinstance(comment, dict):
                            continue

                        comment_user_id = comment.get("keyid")
                        if not comment_user_id:
                            continue
                        comment_user_ref = db.reference(f'users/{comment_user_id}')
                        comment_user = comment_user_ref.get()

                        comment_username = "评论者"
                        comment_avatar = "/static/images/default-avatar.jpeg"
                        if comment_user:
                            comment_username = comment_user.get("name", "评论者")
                            comment_avatar = comment_user.get("avatar", "/static/images/default-avatar.jpeg")

                        comment_data = {
                            "content": comment.get("content", ""),
                            "timestamp": comment.get("timestamp", ""),
                            "username": comment_username,
                            "avatar": comment_avatar
                        }
                        comments.append(comment_data)

                    comments.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

                post['comments'] = comments if comments else []

                user_posts.append(post)

        # Sort posts by timestamp in descending order
        user_posts.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        return jsonify({'status': 'success', 'posts': user_posts})

    except Exception as e:
        print(f"Error in get_user_posts: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/delete_post/<post_id>', methods=['DELETE'])
def delete_post(post_id):
    if 'user' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    try:
        # Get post data from Firebase
        post_ref = db.reference(f'posts/{post_id}')
        post_data = post_ref.get()

        if not post_data:
            return jsonify({'status': 'error', 'message': 'Post not found'})

        # Verify post ownership
        if post_data.get('keyid') != email_prefix:
            return jsonify({'status': 'error', 'message': 'Unauthorized'})

        # Delete associated media files
        media_files = post_data.get('media', [])
        for media_url in media_files:
            if isinstance(media_url, str):
                # Extract filename from URL
                filename = media_url.split('/')[-1]
                media_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                if os.path.exists(media_path):
                    os.remove(media_path)

        # Delete post from Firebase
        post_ref.delete()

        # Delete associated likes and comments
        likes_ref = db.reference(f'likes/{post_id}')
        comments_ref = db.reference(f'comments/{post_id}')

        likes_ref.delete()
        comments_ref.delete()

        return jsonify({'status': 'success'})

    except Exception as e:
        print(f"Error in delete_post: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/get_panda_info')
def get_panda_info():
    # 检查用户是否登录
    if 'user' not in session:
        return jsonify({'success': False, 'message': '用户未登录'})

    # 获取用户邮箱前缀
    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]

    try:
        # 从数据库获取熊猫信息
        panda_ref = db.reference(f'my_panda/{email_prefix}')
        panda_data = panda_ref.get()
        
        # 如果找到数据，返回成功
        if panda_data:
            # 获取用户积分，确保数据一致性
            user_ref = db.reference(f"users/{email_prefix}")
            user_data = user_ref.get() or {}
            user_score = user_data.get('score', 0)
            
            return jsonify({
                'success': True,
                'panda_data': panda_data,
                'user_score': user_score
            })
        else:
            return jsonify({
                'success': False,
                'message': '未找到熊猫数据'
            })
    except Exception as e:
        app.logger.error(f"获取熊猫数据时出错: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'服务器错误: {str(e)}'
        })

@app.route('/update_visited_province', methods=['POST'])
def update_visited_province():
    """更新用户访问过的省份记录"""
    if 'user' not in session:
        return jsonify({'success': False, 'message': '请先登录'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    
    # 获取请求数据
    data = request.get_json()
    province = data.get('province')
    visited_at = data.get('visitedAt')
    stars = data.get('stars')
    reason = data.get('reason')
    
    if not province:
        return jsonify({'success': False, 'message': '缺少省份信息'})
    
    try:
        # 获取现有的访问记录
        travels_ref = db.reference(f'user_travels/{email_prefix}')
        travels_data = travels_ref.get() or {}
        
        # 更新特定省份的记录
        travels_data[province] = {
            'visitedAt': visited_at,
            'stars': stars,
            'reason': reason
        }
        
        # 保存回数据库
        travels_ref.set(travels_data)
        
        return jsonify({'success': True, 'message': f'成功记录访问省份: {province}'})
    except Exception as e:
        app.logger.error(f"更新省份记录出错: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器错误: {str(e)}'})


@app.route('/get_visited_provinces', methods=['GET'])
def get_visited_provinces():
    """获取用户已访问的省份列表及详情"""
    if 'user' not in session:
        return jsonify({'success': False, 'message': '请先登录'})

    user_info = session['user']
    email_prefix = user_info['email'].split('@')[0]
    
    try:
        # 从Firebase获取用户旅行记录
        travels_ref = db.reference(f'user_travels/{email_prefix}')
        travels_data = travels_ref.get() or {}
        
        # 如果没有旅行记录，尝试从熊猫数据中获取简单的访问列表
        if not travels_data:
            panda_ref = db.reference(f'my_panda/{email_prefix}')
            panda_data = panda_ref.get() or {}
            traveled_provinces = panda_data.get('traveled_provinces', [])
            
            # 如果有简单的访问列表，将其转换为详细记录格式
            if traveled_provinces:
                for province in traveled_provinces:
                    travels_data[province] = {
                        'visitedAt': datetime.now().isoformat(),
                        'stars': '★★★',  # 默认星级
                        'reason': '无描述信息'  # 默认描述
                    }
                
                # 保存回数据库
                travels_ref.set(travels_data)
        
        return jsonify({
            'success': True, 
            'visited_provinces': travels_data
        })
    except Exception as e:
        app.logger.error(f"获取省份记录出错: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器错误: {str(e)}'})

@app.route('/get_certificate_data', methods=['GET'])
def get_certificate_data():
    # 检查用户是否登录
    if 'user' not in session:
        return jsonify({'success': False, 'error': '用户未登录'})
        
    try:
        # 获取用户邮箱前缀
        user_email_prefix = session['user']['email'].split('@')[0]
        
        # 从数据库获取用户信息 - 使用后端凭证，不受前端权限限制
        user_ref = db.reference(f'users/{user_email_prefix}')
        user_data = user_ref.get()
        
        if not user_data:
            return jsonify({'success': False, 'error': '用户数据未找到'})
            
        # 获取熊猫信息
        panda_ref = db.reference(f'my_panda/{user_email_prefix}')
        panda_data = panda_ref.get()
        
        if not panda_data or not panda_data.get('name'):
            return jsonify({'success': False, 'error': '熊猫数据未找到'})
            
        # 格式化日期
        adopted_date = '未知日期'
        if panda_data.get('adopted_at'):
            try:
                # 将时间戳转换为日期
                if isinstance(panda_data['adopted_at'], int) or isinstance(panda_data['adopted_at'], float):
                    adopted_date = datetime.fromtimestamp(panda_data['adopted_at']).strftime('%Y年%m月%d日')
                else:
                    # 尝试作为ISO字符串解析
                    adopted_date = datetime.fromisoformat(panda_data['adopted_at'].replace('Z', '+00:00')).strftime('%Y年%m月%d日')
            except Exception as e:
                app.logger.error(f"日期格式化错误: {e}")
                adopted_date = '未知日期'
                
        return jsonify({
            'success': True,
            'username': user_data.get('name', user_email_prefix),
            'pandaName': panda_data['name'],
            'adoptedDate': adopted_date
        })
        
    except Exception as e:
        app.logger.error(f"获取证书数据错误: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


if __name__ == '__main__':
    app.run(debug=True)
