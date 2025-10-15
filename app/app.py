from flask import Flask, render_template, request, jsonify, redirect, url_for
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from models import db, Task
from datetime import datetime, timedelta
import os
import time
from functools import wraps

app = Flask(__name__)

# ===== DATABASE CONFIG =====
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL', 
    'sqlite:////tmp/tasks.db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# ===== PROMETHEUS METRICS =====
REQUEST_COUNT = Counter(
    'app_requests_total',
    'Total requests',
    ['method', 'endpoint', 'status']
)
REQUEST_TIME = Histogram(
    'app_request_duration_seconds',
    'Request duration',
    ['endpoint']
)
TASKS_GAUGE = Gauge('tasks_total', 'Total tasks', ['status'])
ACTIVE_USERS = Gauge('active_users', 'Active users')

# Метрики версії
APP_VERSION = os.getenv('APP_VERSION', '1.0.0')
ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')

# ===== MIDDLEWARE =====
@app.before_request
def before_request():
    request.start_time = time.time()

@app.after_request
def after_request(response):
    if hasattr(request, 'start_time'):
        duration = time.time() - request.start_time
        REQUEST_TIME.labels(endpoint=request.path).observe(duration)
    
    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.path,
        status=response.status_code
    ).inc()
    
    return response

def update_metrics():
    """Оновити метрики"""
    statuses = ['todo', 'in_progress', 'done']
    for status in statuses:
        count = Task.query.filter_by(status=status).count()
        TASKS_GAUGE.labels(status=status).set(count)

# ===== HEALTH CHECKS =====
@app.route('/health')
def health():
    """Liveness probe"""
    try:
        db.session.execute('SELECT 1')
        return jsonify({
            'status': 'healthy',
            'version': APP_VERSION,
            'database': 'connected'
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500

@app.route('/ready')
def ready():
    """Readiness probe"""
    try:
        db.session.execute('SELECT 1')
        return jsonify({
            'status': 'ready',
            'timestamp': datetime.utcnow().isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'not_ready',
            'error': str(e)
        }), 503

@app.route('/metrics')
def metrics():
    """Prometheus метрики"""
    update_metrics()
    return generate_latest()

# ===== API ENDPOINTS =====

@app.route('/')
def index():
    """Головна сторінка"""
    update_metrics()
    tasks = Task.query.order_by(Task.created_at.desc()).all()
    
    stats = {
        'total': Task.query.count(),
        'todo': Task.query.filter_by(status='todo').count(),
        'in_progress': Task.query.filter_by(status='in_progress').count(),
        'done': Task.query.filter_by(status='done').count()
    }
    
    return render_template('index.html', tasks=tasks, stats=stats)

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    """Отримати всі завдання (API)"""
    status = request.args.get('status')
    priority = request.args.get('priority')
    
    query = Task.query
    
    if status:
        query = query.filter_by(status=status)
    if priority:
        query = query.filter_by(priority=priority)
    
    tasks = query.order_by(Task.created_at.desc()).all()
    return jsonify([task.to_dict() for task in tasks])

@app.route('/api/tasks', methods=['POST'])
def create_task():
    """Створити нове завдання (API)"""
    data = request.get_json()
    
    if not data.get('title'):
        return jsonify({'error': 'Title is required'}), 400
    
    try:
        due_date = None
        if data.get('due_date'):
            due_date = datetime.fromisoformat(data['due_date'])
        
        task = Task(
            title=data['title'],
            description=data.get('description', ''),
            priority=data.get('priority', 'medium'),
            due_date=due_date
        )
        
        db.session.add(task)
        db.session.commit()
        
        update_metrics()
        return jsonify(task.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id):
    """Отримати одне завдання"""
    task = Task.query.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    return jsonify(task.to_dict())

@app.route('/api/tasks/<int:task_id>', methods=['PUT'])
def update_task(task_id):
    """Оновити завдання"""
    task = Task.query.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    data = request.get_json()
    
    try:
        if 'title' in data:
            task.title = data['title']
        if 'description' in data:
            task.description = data['description']
        if 'status' in data:
            task.status = data['status']
        if 'priority' in data:
            task.priority = data['priority']
        if 'due_date' in data:
            task.due_date = datetime.fromisoformat(data['due_date']) if data['due_date'] else None
        
        task.updated_at = datetime.utcnow()
        db.session.commit()
        
        update_metrics()
        return jsonify(task.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    """Видалити завдання"""
    task = Task.query.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    try:
        db.session.delete(task)
        db.session.commit()
        update_metrics()
        return jsonify({'status': 'deleted'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

# ===== WEB ROUTES =====

@app.route('/task/new', methods=['GET', 'POST'])
def new_task():
    """Створити нове завдання (веб форма)"""
    if request.method == 'POST':
        try:
            task = Task(
                title=request.form['title'],
                description=request.form.get('description', ''),
                priority=request.form.get('priority', 'medium')
            )
            db.session.add(task)
            db.session.commit()
            update_metrics()
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            return render_template('new_task.html', error=str(e)), 400
    
    return render_template('new_task.html')

@app.route('/task/<int:task_id>')
def view_task(task_id):
    """Переглянути завдання"""
    task = Task.query.get(task_id)
    if not task:
        return "Task not found", 404
    return render_template('task.html', task=task)

@app.route('/task/<int:task_id>/status/<status>')
def update_task_status(task_id, status):
    """Оновити статус завдання"""
    if status not in ['todo', 'in_progress', 'done']:
        return redirect(url_for('index'))
    
    task = Task.query.get(task_id)
    if not task:
        return redirect(url_for('index'))
    
    task.status = status
    task.updated_at = datetime.utcnow()
    db.session.commit()
    update_metrics()
    
    return redirect(url_for('index'))

@app.route('/stats')
def stats():
    """Сторінка статистики"""
    update_metrics()
    
    all_tasks = Task.query.all()
    stats = {
        'total': len(all_tasks),
        'todo': len([t for t in all_tasks if t.status == 'todo']),
        'in_progress': len([t for t in all_tasks if t.status == 'in_progress']),
        'done': len([t for t in all_tasks if t.status == 'done']),
        'high_priority': len([t for t in all_tasks if t.priority == 'high']),
        'overdue': len([t for t in all_tasks if t.due_date and t.due_date < datetime.utcnow() and t.status != 'done']),
    }
    
    return render_template('stats.html', stats=stats)

@app.route('/api/info')
def api_info():
    """Інформація про додаток"""
    return jsonify({
        'app_name': 'Task Manager',
        'version': APP_VERSION,
        'environment': ENVIRONMENT,
        'hostname': os.getenv('HOSTNAME', 'unknown')
    })

# ===== ERROR HANDLERS =====

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500

# ===== INIT DATABASE =====

def init_db():
    """Ініціалізувати базу даних"""
    with app.app_context():
        db.create_all()
        print("✓ Database initialized")

if __name__ == '__main__':
    init_db()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)