import os
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import psycopg
    from psycopg.rows import dict_row
    HAS_PSYCOPG3 = True
except ImportError:
    psycopg = None
    dict_row = None
    HAS_PSYCOPG3 = False

if not HAS_PSYCOPG3:
    import psycopg2
    from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.jinja_options['variable_start_string'] = '[['
app.jinja_options['variable_end_string'] = ']]'

app.secret_key = os.environ.get('SECRET_KEY', 'barbosa-licitacoes-dev-secret-change-in-prod')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
if os.environ.get('RENDER') or os.environ.get('FORCE_HTTPS') == '1':
    app.config['SESSION_COOKIE_SECURE'] = True

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
DB_PATH = os.path.join(os.path.dirname(__file__), 'barbosa_licitacoes.db')

INITIAL_ADMIN_EMAIL = os.environ.get('ADMIN_INITIAL_EMAIL', 'erickdahmerrh@gmail.com').strip().lower()
INITIAL_ADMIN_PASSWORD = os.environ.get('ADMIN_INITIAL_PASSWORD', 'Barbosa@2026')
INITIAL_ADMIN_NAME = os.environ.get('ADMIN_INITIAL_NAME', 'Erick Dahmer')


def is_postgres():
    return bool(DATABASE_URL)


class DBConnection:
    def __init__(self, raw_conn, use_pg, use_pg3=False):
        self.raw = raw_conn
        self.use_pg = use_pg
        self.use_pg3 = use_pg3

    def _sql(self, query):
        return query.replace('?', '%s') if self.use_pg else query

    def execute(self, query, params=()):
        cur = self.raw.cursor()
        cur.execute(self._sql(query), params)
        return cur

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def get_db_connection():
    if is_postgres():
        url = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        if HAS_PSYCOPG3:
            conn = psycopg.connect(url, row_factory=dict_row)
            return DBConnection(conn, use_pg=True, use_pg3=True)
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        return DBConnection(conn, use_pg=True, use_pg3=False)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return DBConnection(conn, use_pg=False, use_pg3=False)


def _garantir_colunas_licitacoes(conn):
    novas_colunas = [
        ("link_edital", "TEXT DEFAULT ''"),
        ("data_esclarecimento", "TEXT DEFAULT ''"),
        ("valor_referencia", "DOUBLE PRECISION DEFAULT 0" if is_postgres() else "REAL DEFAULT 0"),
    ]
    for col, tipo in novas_colunas:
        try:
            conn.execute(f"ALTER TABLE licitacoes ADD COLUMN {col} {tipo}")
            conn.commit()
        except Exception:
            conn.rollback()


def _garantir_usuario_inicial(conn):
    if not INITIAL_ADMIN_EMAIL:
        return
    row = conn.execute(
        'SELECT id FROM usuarios WHERE LOWER(email) = LOWER(?)',
        (INITIAL_ADMIN_EMAIL,)
    ).fetchone()
    if not row:
        senha_hash = generate_password_hash(INITIAL_ADMIN_PASSWORD)
        if is_postgres():
            conn.execute('''
                INSERT INTO usuarios (nome, email, senha_hash, perfil, precisa_trocar_senha, ativo)
                VALUES (?, ?, ?, 'admin', TRUE, TRUE)
                ON CONFLICT (email) DO NOTHING
            ''', (INITIAL_ADMIN_NAME, INITIAL_ADMIN_EMAIL, senha_hash))
        else:
            conn.execute('''
                INSERT OR IGNORE INTO usuarios (nome, email, senha_hash, perfil, precisa_trocar_senha, ativo)
                VALUES (?, ?, ?, 'admin', 1, 1)
            ''', (INITIAL_ADMIN_NAME, INITIAL_ADMIN_EMAIL, senha_hash))
        conn.commit()


def init_db():
    conn = get_db_connection()
    try:
        if is_postgres():
            conn.execute('''
                CREATE TABLE IF NOT EXISTS clientes (
                    id BIGINT PRIMARY KEY,
                    razao_social TEXT NOT NULL,
                    cnpj TEXT NOT NULL,
                    cnae_principal TEXT,
                    contato TEXT,
                    validade_cnd_federal TEXT,
                    validade_cnd_estadual TEXT,
                    validade_cnd_fgts TEXT,
                    validade_cnd_trabalhista TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS licitacoes (
                    id BIGINT PRIMARY KEY,
                    cliente_id BIGINT REFERENCES clientes (id) ON DELETE SET NULL,
                    orgao TEXT NOT NULL,
                    numero_edital TEXT NOT NULL,
                    objeto TEXT NOT NULL,
                    portal TEXT,
                    link_edital TEXT DEFAULT '',
                    data_esclarecimento TEXT DEFAULT '',
                    data_disputa TEXT,
                    valor_referencia DOUBLE PRECISION DEFAULT 0,
                    valor_estimado DOUBLE PRECISION DEFAULT 0,
                    valor_arrematado DOUBLE PRECISION DEFAULT 0,
                    status TEXT DEFAULT 'Analisando Edital',
                    observacoes TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS usuarios (
                    id BIGSERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    senha_hash TEXT NOT NULL,
                    perfil TEXT NOT NULL DEFAULT 'viewer',
                    precisa_trocar_senha BOOLEAN NOT NULL DEFAULT TRUE,
                    ativo BOOLEAN NOT NULL DEFAULT TRUE
                )
            ''')
        else:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    razao_social TEXT NOT NULL,
                    cnpj TEXT NOT NULL,
                    cnae_principal TEXT,
                    contato TEXT,
                    validade_cnd_federal TEXT,
                    validade_cnd_estadual TEXT,
                    validade_cnd_fgts TEXT,
                    validade_cnd_trabalhista TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS licitacoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cliente_id INTEGER,
                    orgao TEXT NOT NULL,
                    numero_edital TEXT NOT NULL,
                    objeto TEXT NOT NULL,
                    portal TEXT,
                    link_edital TEXT DEFAULT '',
                    data_esclarecimento TEXT DEFAULT '',
                    data_disputa TEXT,
                    valor_referencia REAL DEFAULT 0,
                    valor_estimado REAL DEFAULT 0,
                    valor_arrematado REAL DEFAULT 0,
                    status TEXT DEFAULT 'Analisando Edital',
                    observacoes TEXT,
                    FOREIGN KEY (cliente_id) REFERENCES clientes (id)
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS usuarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    senha_hash TEXT NOT NULL,
                    perfil TEXT NOT NULL DEFAULT 'viewer',
                    precisa_trocar_senha INTEGER NOT NULL DEFAULT 1,
                    ativo INTEGER NOT NULL DEFAULT 1
                )
            ''')
        conn.commit()
        _garantir_colunas_licitacoes(conn)
        _garantir_usuario_inicial(conn)
    finally:
        conn.close()


init_db()


def _usuario_atual():
    user_id = session.get('user_id')
    if not user_id:
        return None
    conn = get_db_connection()
    try:
        row = conn.execute(
            'SELECT id, nome, email, perfil, precisa_trocar_senha, ativo FROM usuarios WHERE id = ?',
            (user_id,)
        ).fetchone()
        if not row or not bool(row['ativo']):
            session.clear()
            return None
        return dict(row)
    finally:
        conn.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _usuario_atual()
        if not user:
            return jsonify({'error': 'Não autenticado'}), 401
        if bool(user.get('precisa_trocar_senha')) and request.endpoint != 'trocar_senha':
            return jsonify({'error': 'Troca de senha obrigatória no primeiro acesso', 'precisa_trocar_senha': True}), 428
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if request.current_user.get('perfil') != 'admin':
            return jsonify({'error': 'Acesso restrito a administradores'}), 403
        return f(*args, **kwargs)
    return decorated


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    senha = data.get('senha') or ''
    if not email or not senha:
        return jsonify({'error': 'Informe e-mail e senha'}), 400

    conn = get_db_connection()
    try:
        row = conn.execute(
            'SELECT id, nome, email, senha_hash, perfil, precisa_trocar_senha, ativo FROM usuarios WHERE LOWER(email) = ?',
            (email,)
        ).fetchone()
        if not row or not bool(row['ativo']) or not check_password_hash(row['senha_hash'], senha):
            return jsonify({'error': 'E-mail ou senha inválidos'}), 401

        session.clear()
        session['user_id'] = row['id']
        return jsonify({
            'user': {
                'id': row['id'],
                'nome': row['nome'],
                'email': row['email'],
                'perfil': row['perfil'],
                'precisa_trocar_senha': bool(row['precisa_trocar_senha'])
            }
        })
    finally:
        conn.close()


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'status': 'logged_out'})


@app.route('/api/auth/me', methods=['GET'])
def me():
    user = _usuario_atual()
    if not user:
        return jsonify({'authenticated': False, 'user': None}), 200
    return jsonify({
        'authenticated': True,
        'user': {
            'id': user['id'],
            'nome': user['nome'],
            'email': user['email'],
            'perfil': user['perfil'],
            'precisa_trocar_senha': bool(user.get('precisa_trocar_senha'))
        }
    })


@app.route('/api/auth/trocar-senha', methods=['POST'])
@login_required
def trocar_senha():
    data = request.json or {}
    nova_senha = data.get('nova_senha') or ''
    if len(nova_senha) < 6:
        return jsonify({'error': 'A nova senha deve ter pelo menos 6 caracteres'}), 400

    senha_hash = generate_password_hash(nova_senha)
    conn = get_db_connection()
    try:
        if is_postgres():
            conn.execute(
                'UPDATE usuarios SET senha_hash = ?, precisa_trocar_senha = FALSE WHERE id = ?',
                (senha_hash, request.current_user['id'])
            )
        else:
            conn.execute(
                'UPDATE usuarios SET senha_hash = ?, precisa_trocar_senha = 0 WHERE id = ?',
                (senha_hash, request.current_user['id'])
            )
        conn.commit()
        return jsonify({'status': 'password_updated', 'precisa_trocar_senha': False})
    finally:
        conn.close()


@app.route('/api/usuarios', methods=['GET', 'POST'])
@admin_required
def handle_usuarios():
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            rows = conn.execute(
                'SELECT id, nome, email, perfil, precisa_trocar_senha, ativo FROM usuarios ORDER BY nome'
            ).fetchall()
            return jsonify([
                {
                    'id': r['id'],
                    'nome': r['nome'],
                    'email': r['email'],
                    'perfil': r['perfil'],
                    'precisa_trocar_senha': bool(r['precisa_trocar_senha']),
                    'ativo': bool(r['ativo'])
                }
                for r in rows
            ])

        if request.method == 'POST':
            data = request.json or {}
            nome = (data.get('nome') or '').strip()
            email = (data.get('email') or '').strip().lower()
            senha_provisoria = data.get('senha_provisoria') or ''
            perfil = data.get('perfil') if data.get('perfil') in ('admin', 'viewer') else 'viewer'

            if not nome or not email or len(senha_provisoria) < 6:
                return jsonify({'error': 'Preencha nome, e-mail e senha provisória (mín. 6 caracteres)'}), 400

            existente = conn.execute('SELECT id FROM usuarios WHERE LOWER(email) = ?', (email,)).fetchone()
            if existente:
                return jsonify({'error': 'Já existe um usuário com este e-mail'}), 409

            senha_hash = generate_password_hash(senha_provisoria)
            if is_postgres():
                conn.execute('''
                    INSERT INTO usuarios (nome, email, senha_hash, perfil, precisa_trocar_senha, ativo)
                    VALUES (?, ?, ?, ?, TRUE, TRUE)
                ''', (nome, email, senha_hash, perfil))
            else:
                conn.execute('''
                    INSERT INTO usuarios (nome, email, senha_hash, perfil, precisa_trocar_senha, ativo)
                    VALUES (?, ?, ?, ?, 1, 1)
                ''', (nome, email, senha_hash, perfil))
            conn.commit()
            return jsonify({'status': 'created'}), 201
    finally:
        conn.close()


@app.route('/api/usuarios/<int:user_id>', methods=['PATCH'])
@admin_required
def atualizar_usuario(user_id):
    data = request.json or {}
    conn = get_db_connection()
    try:
        row = conn.execute('SELECT id, ativo FROM usuarios WHERE id = ?', (user_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Usuário não encontrado'}), 404

        if 'ativo' in data:
            novo_ativo = bool(data['ativo'])
            if is_postgres():
                conn.execute('UPDATE usuarios SET ativo = ? WHERE id = ?', (novo_ativo, user_id))
            else:
                conn.execute('UPDATE usuarios SET ativo = ? WHERE id = ?', (1 if novo_ativo else 0, user_id))

        if data.get('resetar_senha'):
            nova_prov = data.get('senha_provisoria') or 'Barbosa@2026'
            if len(nova_prov) < 6:
                return jsonify({'error': 'Senha provisória deve ter pelo menos 6 caracteres'}), 400
            senha_hash = generate_password_hash(nova_prov)
            if is_postgres():
                conn.execute(
                    'UPDATE usuarios SET senha_hash = ?, precisa_trocar_senha = TRUE WHERE id = ?',
                    (senha_hash, user_id)
                )
            else:
                conn.execute(
                    'UPDATE usuarios SET senha_hash = ?, precisa_trocar_senha = 1 WHERE id = ?',
                    (senha_hash, user_id)
                )

        conn.commit()
        return jsonify({'status': 'updated'})
    finally:
        conn.close()


@app.route('/api/clientes', methods=['GET', 'POST'])
@login_required
def handle_clientes():
    if request.method == 'POST' and request.current_user.get('perfil') != 'admin':
        return jsonify({'error': 'Apenas administradores podem cadastrar/editar clientes'}), 403

    conn = get_db_connection()
    try:
        if request.method == 'GET':
            rows = conn.execute('SELECT * FROM clientes ORDER BY razao_social').fetchall()
            return jsonify([dict(row) for row in rows])

        if request.method == 'POST':
            data = request.json or {}
            novo_id = int(data.get('id') or 0) or None
            if is_postgres() and novo_id:
                conn.execute('''
                    INSERT INTO clientes (
                        id, razao_social, cnpj, cnae_principal, contato,
                        validade_cnd_federal, validade_cnd_estadual,
                        validade_cnd_fgts, validade_cnd_trabalhista
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        razao_social = EXCLUDED.razao_social,
                        cnpj = EXCLUDED.cnpj,
                        cnae_principal = EXCLUDED.cnae_principal,
                        contato = EXCLUDED.contato,
                        validade_cnd_federal = EXCLUDED.validade_cnd_federal,
                        validade_cnd_estadual = EXCLUDED.validade_cnd_estadual,
                        validade_cnd_fgts = EXCLUDED.validade_cnd_fgts,
                        validade_cnd_trabalhista = EXCLUDED.validade_cnd_trabalhista
                ''', (
                    novo_id, data.get('razao_social'), data.get('cnpj'), data.get('cnae_principal'),
                    data.get('contato'), data.get('validade_cnd_federal'),
                    data.get('validade_cnd_estadual'), data.get('validade_cnd_fgts'),
                    data.get('validade_cnd_trabalhista')
                ))
            else:
                conn.execute('''
                    INSERT INTO clientes (
                        razao_social, cnpj, cnae_principal, contato,
                        validade_cnd_federal, validade_cnd_estadual,
                        validade_cnd_fgts, validade_cnd_trabalhista
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data.get('razao_social'), data.get('cnpj'), data.get('cnae_principal'),
                    data.get('contato'), data.get('validade_cnd_federal'),
                    data.get('validade_cnd_estadual'), data.get('validade_cnd_fgts'),
                    data.get('validade_cnd_trabalhista')
                ))
            conn.commit()
            return jsonify({'status': 'success'}), 201
    finally:
        conn.close()


@app.route('/api/clientes/<int:id>', methods=['DELETE'])
@admin_required
def delete_cliente(id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM clientes WHERE id = ?', (id,))
        conn.commit()
        return jsonify({'status': 'deleted'})
    finally:
        conn.close()


@app.route('/api/licitacoes', methods=['GET', 'POST'])
@login_required
def handle_licitacoes():
    if request.method == 'POST' and request.current_user.get('perfil') != 'admin':
        return jsonify({'error': 'Apenas administradores podem cadastrar/editar licitações'}), 403

    conn = get_db_connection()
    try:
        if request.method == 'GET':
            query = '''
                SELECT l.*, c.razao_social as cliente_nome
                FROM licitacoes l
                LEFT JOIN clientes c ON l.cliente_id = c.id
                ORDER BY l.data_disputa ASC
            '''
            rows = conn.execute(query).fetchall()
            return jsonify([dict(row) for row in rows])

        if request.method == 'POST':
            data = request.json or {}
            novo_id = int(data.get('id') or 0) or None
            cliente_id = data.get('cliente_id')
            cliente_id = int(cliente_id) if cliente_id not in (None, '', 'null') else None

            if is_postgres() and novo_id:
                conn.execute('''
                    INSERT INTO licitacoes (
                        id, cliente_id, orgao, numero_edital, objeto, portal,
                        link_edital, data_esclarecimento, data_disputa,
                        valor_referencia, valor_estimado, valor_arrematado,
                        status, observacoes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        cliente_id = EXCLUDED.cliente_id,
                        orgao = EXCLUDED.orgao,
                        numero_edital = EXCLUDED.numero_edital,
                        objeto = EXCLUDED.objeto,
                        portal = EXCLUDED.portal,
                        link_edital = EXCLUDED.link_edital,
                        data_esclarecimento = EXCLUDED.data_esclarecimento,
                        data_disputa = EXCLUDED.data_disputa,
                        valor_referencia = EXCLUDED.valor_referencia,
                        valor_estimado = EXCLUDED.valor_estimado,
                        valor_arrematado = EXCLUDED.valor_arrematado,
                        status = EXCLUDED.status,
                        observacoes = EXCLUDED.observacoes
                ''', (
                    novo_id, cliente_id, data.get('orgao'), data.get('numero_edital'),
                    data.get('objeto'), data.get('portal'),
                    (data.get('link_edital') or '').strip(),
                    data.get('data_esclarecimento') or '',
                    data.get('data_disputa') or '',
                    float(data.get('valor_referencia') or 0),
                    float(data.get('valor_estimado') or 0),
                    float(data.get('valor_arrematado') or 0),
                    data.get('status', 'Analisando Edital'),
                    data.get('observacoes', '')
                ))
            else:
                conn.execute('''
                    INSERT INTO licitacoes (
                        cliente_id, orgao, numero_edital, objeto, portal,
                        link_edital, data_esclarecimento, data_disputa,
                        valor_referencia, valor_estimado, valor_arrematado,
                        status, observacoes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    cliente_id, data.get('orgao'), data.get('numero_edital'),
                    data.get('objeto'), data.get('portal'),
                    (data.get('link_edital') or '').strip(),
                    data.get('data_esclarecimento') or '',
                    data.get('data_disputa') or '',
                    float(data.get('valor_referencia') or 0),
                    float(data.get('valor_estimado') or 0),
                    float(data.get('valor_arrematado') or 0),
                    data.get('status', 'Analisando Edital'),
                    data.get('observacoes', '')
                ))
            conn.commit()
            return jsonify({'status': 'success'}), 201
    finally:
        conn.close()


@app.route('/api/licitacoes/<int:id>', methods=['PUT', 'DELETE'])
@admin_required
def update_delete_licitacao(id):
    conn = get_db_connection()
    try:
        if request.method == 'PUT':
            data = request.json or {}
            conn.execute('''
                UPDATE licitacoes
                SET link_edital = ?,
                    data_esclarecimento = ?,
                    data_disputa = ?,
                    valor_referencia = ?,
                    valor_estimado = ?,
                    valor_arrematado = ?,
                    status = ?,
                    observacoes = ?
                WHERE id = ?
            ''', (
                (data.get('link_edital') or '').strip(),
                data.get('data_esclarecimento') or '',
                data.get('data_disputa') or '',
                float(data.get('valor_referencia') or 0),
                float(data.get('valor_estimado') or 0),
                float(data.get('valor_arrematado') or 0),
                data.get('status', 'Analisando Edital'),
                data.get('observacoes', ''),
                id
            ))
            conn.commit()
            return jsonify({'status': 'updated'})

        if request.method == 'DELETE':
            conn.execute('DELETE FROM licitacoes WHERE id = ?', (id,))
            conn.commit()
            return jsonify({'status': 'deleted'})
    finally:
        conn.close()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)