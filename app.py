import os
import re
import uuid
import sqlite3
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    import psycopg
    from psycopg.rows import dict_row
    HAS_PSYCOPG3 = True
except ImportError:
    psycopg = None
    dict_row = None
    HAS_PSYCOPG3 = False

psycopg2 = None
RealDictCursor = None
if not HAS_PSYCOPG3:
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        pass

app = Flask(__name__)
app.jinja_options['variable_start_string'] = '[['
app.jinja_options['variable_end_string'] = ']]'

app.secret_key = os.environ.get('SECRET_KEY', 'barbosa-licitacoes-dev-secret-change-in-prod')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB por envio

if os.environ.get('RENDER') or os.environ.get('FORCE_HTTPS') == '1':
    app.config['SESSION_COOKIE_SECURE'] = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads_culturais')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
DB_PATH = os.path.join(BASE_DIR, 'barbosa_licitacoes.db')

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


def _garantir_colunas_existentes(conn):
    colunas_licitacoes = [
        ("link_edital", "TEXT DEFAULT ''"),
        ("data_esclarecimento", "TEXT DEFAULT ''"),
        ("valor_referencia", "DOUBLE PRECISION DEFAULT 0" if is_postgres() else "REAL DEFAULT 0"),
    ]
    for col, tipo in colunas_licitacoes:
        try:
            conn.execute(f"ALTER TABLE licitacoes ADD COLUMN {col} {tipo}")
            conn.commit()
        except Exception:
            conn.rollback()

    colunas_usuarios = [
        ("acesso_licitacoes", "BOOLEAN NOT NULL DEFAULT TRUE" if is_postgres() else "INTEGER NOT NULL DEFAULT 1"),
        ("acesso_cultural", "BOOLEAN NOT NULL DEFAULT TRUE" if is_postgres() else "INTEGER NOT NULL DEFAULT 1"),
    ]
    for col, tipo in colunas_usuarios:
        try:
            conn.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {tipo}")
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
                INSERT INTO usuarios (nome, email, senha_hash, perfil, precisa_trocar_senha, ativo, acesso_licitacoes, acesso_cultural)
                VALUES (?, ?, ?, 'admin', TRUE, TRUE, TRUE, TRUE)
                ON CONFLICT (email) DO NOTHING
            ''', (INITIAL_ADMIN_NAME, INITIAL_ADMIN_EMAIL, senha_hash))
        else:
            conn.execute('''
                INSERT OR IGNORE INTO usuarios (nome, email, senha_hash, perfil, precisa_trocar_senha, ativo, acesso_licitacoes, acesso_cultural)
                VALUES (?, ?, ?, 'admin', 1, 1, 1, 1)
            ''', (INITIAL_ADMIN_NAME, INITIAL_ADMIN_EMAIL, senha_hash))
        conn.commit()


def init_db():
    conn = get_db_connection()
    try:
        if is_postgres():
            conn.execute('''
                CREATE TABLE IF NOT EXISTS clientes (
                    id BIGSERIAL PRIMARY KEY,
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
                    id BIGSERIAL PRIMARY KEY,
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
                    ativo BOOLEAN NOT NULL DEFAULT TRUE,
                    acesso_licitacoes BOOLEAN NOT NULL DEFAULT TRUE,
                    acesso_cultural BOOLEAN NOT NULL DEFAULT TRUE
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS projetos_culturais (
                    id BIGSERIAL PRIMARY KEY,
                    edital TEXT NOT NULL,
                    titulo_projeto TEXT NOT NULL,
                    proponente TEXT DEFAULT '',
                    municipio_rs TEXT NOT NULL,
                    data_fim_inscricao TEXT DEFAULT '',
                    data_recurso TEXT DEFAULT '',
                    prazo_conclusao_projeto TEXT DEFAULT '',
                    valor_solicitado DOUBLE PRECISION DEFAULT 0,
                    valor_aprovado DOUBLE PRECISION DEFAULT 0,
                    status TEXT DEFAULT 'Elaboração',
                    dt_abertura_inscricoes TEXT DEFAULT '',
                    dt_impugnacao TEXT DEFAULT '',
                    dt_envio_proposta TEXT DEFAULT '',
                    dt_selecao TEXT DEFAULT '',
                    dt_recurso TEXT DEFAULT '',
                    dt_classificacao TEXT DEFAULT '',
                    dt_habilitacao TEXT DEFAULT '',
                    dt_execucao TEXT DEFAULT '',
                    dt_prestacao_contas TEXT DEFAULT '',
                    observacoes TEXT DEFAULT ''
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cultural_certidoes (
                    id BIGSERIAL PRIMARY KEY,
                    projeto_id BIGINT REFERENCES projetos_culturais (id) ON DELETE CASCADE,
                    nome_documento TEXT NOT NULL,
                    orgao_emissor TEXT DEFAULT '',
                    data_emissao TEXT DEFAULT '',
                    data_vencimento TEXT NOT NULL,
                    arquivo_nome TEXT DEFAULT '',
                    arquivo_path TEXT DEFAULT '',
                    observacoes TEXT DEFAULT ''
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cultural_custos (
                    id BIGSERIAL PRIMARY KEY,
                    projeto_id BIGINT REFERENCES projetos_culturais (id) ON DELETE CASCADE,
                    rubrica TEXT NOT NULL,
                    categoria TEXT DEFAULT 'Produção',
                    unidade TEXT DEFAULT 'Unidade',
                    quantidade DOUBLE PRECISION DEFAULT 1,
                    valor_unitario DOUBLE PRECISION DEFAULT 0,
                    valor_total DOUBLE PRECISION DEFAULT 0,
                    valor_executado DOUBLE PRECISION DEFAULT 0,
                    observacoes TEXT DEFAULT ''
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cultural_documentos (
                    id BIGSERIAL PRIMARY KEY,
                    projeto_id BIGINT REFERENCES projetos_culturais (id) ON DELETE CASCADE,
                    etapa TEXT NOT NULL DEFAULT 'habilitacao',
                    tipo_documento TEXT NOT NULL,
                    titulo TEXT NOT NULL,
                    arquivo_nome TEXT DEFAULT '',
                    arquivo_path TEXT DEFAULT '',
                    link_url TEXT DEFAULT '',
                    valor_comprovante DOUBLE PRECISION DEFAULT 0,
                    data_documento TEXT DEFAULT '',
                    criado_em TEXT DEFAULT ''
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
                    ativo INTEGER NOT NULL DEFAULT 1,
                    acesso_licitacoes INTEGER NOT NULL DEFAULT 1,
                    acesso_cultural INTEGER NOT NULL DEFAULT 1
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS projetos_culturais (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    edital TEXT NOT NULL,
                    titulo_projeto TEXT NOT NULL,
                    proponente TEXT DEFAULT '',
                    municipio_rs TEXT NOT NULL,
                    data_fim_inscricao TEXT DEFAULT '',
                    data_recurso TEXT DEFAULT '',
                    prazo_conclusao_projeto TEXT DEFAULT '',
                    valor_solicitado REAL DEFAULT 0,
                    valor_aprovado REAL DEFAULT 0,
                    status TEXT DEFAULT 'Elaboração',
                    dt_abertura_inscricoes TEXT DEFAULT '',
                    dt_impugnacao TEXT DEFAULT '',
                    dt_envio_proposta TEXT DEFAULT '',
                    dt_selecao TEXT DEFAULT '',
                    dt_recurso TEXT DEFAULT '',
                    dt_classificacao TEXT DEFAULT '',
                    dt_habilitacao TEXT DEFAULT '',
                    dt_execucao TEXT DEFAULT '',
                    dt_prestacao_contas TEXT DEFAULT '',
                    observacoes TEXT DEFAULT ''
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cultural_certidoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    projeto_id INTEGER,
                    nome_documento TEXT NOT NULL,
                    orgao_emissor TEXT DEFAULT '',
                    data_emissao TEXT DEFAULT '',
                    data_vencimento TEXT NOT NULL,
                    arquivo_nome TEXT DEFAULT '',
                    arquivo_path TEXT DEFAULT '',
                    observacoes TEXT DEFAULT '',
                    FOREIGN KEY (projeto_id) REFERENCES projetos_culturais (id) ON DELETE CASCADE
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cultural_custos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    projeto_id INTEGER,
                    rubrica TEXT NOT NULL,
                    categoria TEXT DEFAULT 'Produção',
                    unidade TEXT DEFAULT 'Unidade',
                    quantidade REAL DEFAULT 1,
                    valor_unitario REAL DEFAULT 0,
                    valor_total REAL DEFAULT 0,
                    valor_executado REAL DEFAULT 0,
                    observacoes TEXT DEFAULT '',
                    FOREIGN KEY (projeto_id) REFERENCES projetos_culturais (id) ON DELETE CASCADE
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cultural_documentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    projeto_id INTEGER,
                    etapa TEXT NOT NULL DEFAULT 'habilitacao',
                    tipo_documento TEXT NOT NULL,
                    titulo TEXT NOT NULL,
                    arquivo_nome TEXT DEFAULT '',
                    arquivo_path TEXT DEFAULT '',
                    link_url TEXT DEFAULT '',
                    valor_comprovante REAL DEFAULT 0,
                    data_documento TEXT DEFAULT '',
                    criado_em TEXT DEFAULT '',
                    FOREIGN KEY (projeto_id) REFERENCES projetos_culturais (id) ON DELETE CASCADE
                )
            ''')
        conn.commit()
        _garantir_colunas_existentes(conn)
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
            '''SELECT id, nome, email, perfil, precisa_trocar_senha, ativo,
                      acesso_licitacoes, acesso_cultural
               FROM usuarios WHERE id = ?''',
            (user_id,)
        ).fetchone()
        if not row or not bool(row['ativo']):
            session.clear()
            return None
        user = dict(row)
        user['acesso_licitacoes'] = bool(user.get('acesso_licitacoes', True))
        user['acesso_cultural'] = bool(user.get('acesso_cultural', True))
        return user
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


def modulo_required(modulo):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            user = request.current_user
            if user.get('perfil') == 'admin':
                return f(*args, **kwargs)
            if modulo == 'licitacoes' and not user.get('acesso_licitacoes'):
                return jsonify({'error': 'Módulo de Licitações não liberado para este usuário'}), 403
            if modulo == 'cultural' and not user.get('acesso_cultural'):
                return jsonify({'error': 'Módulo de Produções Culturais não liberado para este usuário'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def _salvar_arquivo_local(file_storage, subpasta='geral'):
    if not file_storage or not file_storage.filename:
        return '', ''
    nome_seguro = secure_filename(file_storage.filename)
    if not nome_seguro:
        nome_seguro = 'documento.bin'
    pasta_destino = os.path.join(UPLOAD_FOLDER, subpasta)
    os.makedirs(pasta_destino, exist_ok=True)
    nome_unico = f"{uuid.uuid4().hex[:10]}_{nome_seguro}"
    caminho_completo = os.path.join(pasta_destino, nome_unico)
    file_storage.save(caminho_completo)
    caminho_relativo = f"{subpasta}/{nome_unico}"
    return file_storage.filename, caminho_relativo


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/uploads/<path:filename>')
@login_required
def baixar_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ==============================================================================
# AUTENTICAÇÃO E GESTÃO DE USUÁRIOS / PERMISSÕES DE MÓDULOS
# ==============================================================================

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
            '''SELECT id, nome, email, senha_hash, perfil, precisa_trocar_senha, ativo,
                      acesso_licitacoes, acesso_cultural
               FROM usuarios WHERE LOWER(email) = ?''',
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
                'precisa_trocar_senha': bool(row['precisa_trocar_senha']),
                'acesso_licitacoes': bool(row['acesso_licitacoes']),
                'acesso_cultural': bool(row['acesso_cultural'])
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
            'precisa_trocar_senha': bool(user.get('precisa_trocar_senha')),
            'acesso_licitacoes': bool(user.get('acesso_licitacoes', True)),
            'acesso_cultural': bool(user.get('acesso_cultural', True))
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
                '''SELECT id, nome, email, perfil, precisa_trocar_senha, ativo,
                          acesso_licitacoes, acesso_cultural
                   FROM usuarios ORDER BY nome'''
            ).fetchall()
            return jsonify([
                {
                    'id': r['id'],
                    'nome': r['nome'],
                    'email': r['email'],
                    'perfil': r['perfil'],
                    'precisa_trocar_senha': bool(r['precisa_trocar_senha']),
                    'ativo': bool(r['ativo']),
                    'acesso_licitacoes': bool(r['acesso_licitacoes']),
                    'acesso_cultural': bool(r['acesso_cultural'])
                }
                for r in rows
            ])

        if request.method == 'POST':
            data = request.json or {}
            nome = (data.get('nome') or '').strip()
            email = (data.get('email') or '').strip().lower()
            senha_provisoria = data.get('senha_provisoria') or ''
            perfil = data.get('perfil') if data.get('perfil') in ('admin', 'viewer') else 'viewer'
            acesso_lic = bool(data.get('acesso_licitacoes', True))
            acesso_cult = bool(data.get('acesso_cultural', True))

            if not nome or not email or len(senha_provisoria) < 6:
                return jsonify({'error': 'Preencha nome, e-mail e senha provisória (mín. 6 caracteres)'}), 400

            existente = conn.execute('SELECT id FROM usuarios WHERE LOWER(email) = ?', (email,)).fetchone()
            if existente:
                return jsonify({'error': 'Já existe um usuário com este e-mail'}), 409

            senha_hash = generate_password_hash(senha_provisoria)
            if is_postgres():
                conn.execute('''
                    INSERT INTO usuarios (nome, email, senha_hash, perfil, precisa_trocar_senha, ativo, acesso_licitacoes, acesso_cultural)
                    VALUES (?, ?, ?, ?, TRUE, TRUE, ?, ?)
                ''', (nome, email, senha_hash, perfil, acesso_lic, acesso_cult))
            else:
                conn.execute('''
                    INSERT INTO usuarios (nome, email, senha_hash, perfil, precisa_trocar_senha, ativo, acesso_licitacoes, acesso_cultural)
                    VALUES (?, ?, ?, ?, 1, 1, ?, ?)
                ''', (nome, email, senha_hash, perfil, 1 if acesso_lic else 0, 1 if acesso_cult else 0))
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
        row = conn.execute('SELECT id FROM usuarios WHERE id = ?', (user_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Usuário não encontrado'}), 404

        if 'ativo' in data:
            novo_ativo = bool(data['ativo'])
            val = novo_ativo if is_postgres() else (1 if novo_ativo else 0)
            conn.execute('UPDATE usuarios SET ativo = ? WHERE id = ?', (val, user_id))

        if 'acesso_licitacoes' in data:
            novo_lic = bool(data['acesso_licitacoes'])
            val = novo_lic if is_postgres() else (1 if novo_lic else 0)
            conn.execute('UPDATE usuarios SET acesso_licitacoes = ? WHERE id = ?', (val, user_id))

        if 'acesso_cultural' in data:
            novo_cult = bool(data['acesso_cultural'])
            val = novo_cult if is_postgres() else (1 if novo_cult else 0)
            conn.execute('UPDATE usuarios SET acesso_cultural = ? WHERE id = ?', (val, user_id))

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


# ==============================================================================
# MÓDULO 1: LICITAÇÕES E CLIENTES (EXISTENTE)
# ==============================================================================

@app.route('/api/clientes', methods=['GET', 'POST'])
@modulo_required('licitacoes')
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
@modulo_required('licitacoes')
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
            cliente_id = data.get('cliente_id')
            cliente_id = int(cliente_id) if cliente_id not in (None, '', 'null') else None

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


# ==============================================================================
# MÓDULO 2: PRODUÇÕES CULTURAIS
# ==============================================================================

def _calcular_alerta_vencimento(data_venc_str):
    if not data_venc_str:
        return {'status_alerta': 'Sem data', 'dias_restantes': None, 'nivel': 'secondary'}
    try:
        dt_venc = datetime.strptime(data_venc_str[:10], '%Y-%m-%d').date()
        hoje = date.today()
        dias = (dt_venc - hoje).days
        if dias < 0:
            return {'status_alerta': f'Vencida há {abs(dias)} dia(s)', 'dias_restantes': dias, 'nivel': 'danger'}
        if dias <= 15:
            return {'status_alerta': f'Crítico: vence em {dias} dia(s)', 'dias_restantes': dias, 'nivel': 'danger'}
        if dias <= 30:
            return {'status_alerta': f'Atenção: vence em {dias} dia(s)', 'dias_restantes': dias, 'nivel': 'warning'}
        return {'status_alerta': f'Válida ({dias} dias)', 'dias_restantes': dias, 'nivel': 'success'}
    except Exception:
        return {'status_alerta': 'Data inválida', 'dias_restantes': None, 'nivel': 'secondary'}


@app.route('/api/cultural/projetos', methods=['GET', 'POST'])
@modulo_required('cultural')
def handle_projetos_culturais():
    if request.method == 'POST' and request.current_user.get('perfil') != 'admin':
        return jsonify({'error': 'Apenas administradores podem cadastrar projetos culturais'}), 403

    conn = get_db_connection()
    try:
        if request.method == 'GET':
            projetos = [dict(r) for r in conn.execute(
                'SELECT * FROM projetos_culturais ORDER BY id DESC'
            ).fetchall()]

            certidoes = [dict(r) for r in conn.execute(
                'SELECT * FROM cultural_certidoes ORDER BY data_vencimento ASC'
            ).fetchall()]
            for c in certidoes:
                c.update(_calcular_alerta_vencimento(c.get('data_vencimento')))

            custos = [dict(r) for r in conn.execute(
                'SELECT * FROM cultural_custos ORDER BY id ASC'
            ).fetchall()]

            documentos = [dict(r) for r in conn.execute(
                'SELECT * FROM cultural_documentos ORDER BY id DESC'
            ).fetchall()]

            for p in projetos:
                pid = p['id']
                p['certidoes'] = [c for c in certidoes if c['projeto_id'] == pid]
                p['custos'] = [c for c in custos if c['projeto_id'] == pid]
                p['documentos'] = [d for d in documentos if d['projeto_id'] == pid]
                p['total_orcado'] = sum(float(x.get('valor_total') or 0) for x in p['custos'])
                p['total_executado'] = sum(float(x.get('valor_executado') or 0) for x in p['custos'])
                p['alertas_certidoes'] = sum(1 for c in p['certidoes'] if c['nivel'] in ('danger', 'warning'))

            return jsonify(projetos)

        if request.method == 'POST':
            data = request.json or {}
            conn.execute('''
                INSERT INTO projetos_culturais (
                    edital, titulo_projeto, proponente, municipio_rs,
                    data_fim_inscricao, data_recurso, prazo_conclusao_projeto,
                    valor_solicitado, valor_aprovado, status,
                    dt_abertura_inscricoes, dt_impugnacao, dt_envio_proposta,
                    dt_selecao, dt_recurso, dt_classificacao,
                    dt_habilitacao, dt_execucao, dt_prestacao_contas, observacoes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                (data.get('edital') or '').strip(),
                (data.get('titulo_projeto') or '').strip(),
                (data.get('proponente') or '').strip(),
                (data.get('municipio_rs') or '').strip(),
                data.get('data_fim_inscricao') or '',
                data.get('data_recurso') or '',
                data.get('prazo_conclusao_projeto') or '',
                float(data.get('valor_solicitado') or 0),
                float(data.get('valor_aprovado') or 0),
                data.get('status') or 'Elaboração',
                data.get('dt_abertura_inscricoes') or '',
                data.get('dt_impugnacao') or '',
                data.get('dt_envio_proposta') or '',
                data.get('dt_selecao') or '',
                data.get('dt_recurso') or data.get('data_recurso') or '',
                data.get('dt_classificacao') or '',
                data.get('dt_habilitacao') or '',
                data.get('dt_execucao') or '',
                data.get('dt_prestacao_contas') or '',
                data.get('observacoes') or ''
            ))
            conn.commit()
            return jsonify({'status': 'created'}), 201
    finally:
        conn.close()


@app.route('/api/cultural/projetos/<int:projeto_id>', methods=['PUT', 'DELETE'])
@admin_required
def update_delete_projeto_cultural(projeto_id):
    conn = get_db_connection()
    try:
        if request.method == 'PUT':
            data = request.json or {}
            conn.execute('''
                UPDATE projetos_culturais
                SET edital = ?,
                    titulo_projeto = ?,
                    proponente = ?,
                    municipio_rs = ?,
                    data_fim_inscricao = ?,
                    data_recurso = ?,
                    prazo_conclusao_projeto = ?,
                    valor_solicitado = ?,
                    valor_aprovado = ?,
                    status = ?,
                    dt_abertura_inscricoes = ?,
                    dt_impugnacao = ?,
                    dt_envio_proposta = ?,
                    dt_selecao = ?,
                    dt_recurso = ?,
                    dt_classificacao = ?,
                    dt_habilitacao = ?,
                    dt_execucao = ?,
                    dt_prestacao_contas = ?,
                    observacoes = ?
                WHERE id = ?
            ''', (
                (data.get('edital') or '').strip(),
                (data.get('titulo_projeto') or '').strip(),
                (data.get('proponente') or '').strip(),
                (data.get('municipio_rs') or '').strip(),
                data.get('data_fim_inscricao') or '',
                data.get('data_recurso') or '',
                data.get('prazo_conclusao_projeto') or '',
                float(data.get('valor_solicitado') or 0),
                float(data.get('valor_aprovado') or 0),
                data.get('status') or 'Elaboração',
                data.get('dt_abertura_inscricoes') or '',
                data.get('dt_impugnacao') or '',
                data.get('dt_envio_proposta') or '',
                data.get('dt_selecao') or '',
                data.get('dt_recurso') or '',
                data.get('dt_classificacao') or '',
                data.get('dt_habilitacao') or '',
                data.get('dt_execucao') or '',
                data.get('dt_prestacao_contas') or '',
                data.get('observacoes') or '',
                projeto_id
            ))
            conn.commit()
            return jsonify({'status': 'updated'})

        if request.method == 'DELETE':
            conn.execute('DELETE FROM projetos_culturais WHERE id = ?', (projeto_id,))
            conn.commit()
            return jsonify({'status': 'deleted'})
    finally:
        conn.close()


# --- Documentação de Habilitação e Certidões com Alerta de Vencimento ---

@app.route('/api/cultural/projetos/<int:projeto_id>/certidoes', methods=['POST'])
@admin_required
def add_certidao_cultural(projeto_id):
    nome_documento = (request.form.get('nome_documento') or '').strip()
    orgao_emissor = (request.form.get('orgao_emissor') or '').strip()
    data_emissao = request.form.get('data_emissao') or ''
    data_vencimento = request.form.get('data_vencimento') or ''
    observacoes = request.form.get('observacoes') or ''

    if not nome_documento or not data_vencimento:
        return jsonify({'error': 'Informe o nome da certidão/documento e a data de vencimento'}), 400

    arquivo = request.files.get('arquivo')
    arquivo_nome, arquivo_path = _salvar_arquivo_local(arquivo, subpasta=f'projeto_{projeto_id}/certidoes')

    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT INTO cultural_certidoes (
                projeto_id, nome_documento, orgao_emissor, data_emissao,
                data_vencimento, arquivo_nome, arquivo_path, observacoes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            projeto_id, nome_documento, orgao_emissor, data_emissao,
            data_vencimento, arquivo_nome, arquivo_path, observacoes
        ))
        conn.commit()
        return jsonify({'status': 'created'}), 201
    finally:
        conn.close()


@app.route('/api/cultural/certidoes/<int:cert_id>', methods=['DELETE'])
@admin_required
def delete_certidao_cultural(cert_id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM cultural_certidoes WHERE id = ?', (cert_id,))
        conn.commit()
        return jsonify({'status': 'deleted'})
    finally:
        conn.close()


# --- Planilha de Custos ---

@app.route('/api/cultural/projetos/<int:projeto_id>/custos', methods=['POST'])
@admin_required
def add_custo_cultural(projeto_id):
    data = request.json or {}
    rubrica = (data.get('rubrica') or '').strip()
    if not rubrica:
        return jsonify({'error': 'Informe a rubrica / item de custo'}), 400

    qtd = float(data.get('quantidade') or 1)
    v_unit = float(data.get('valor_unitario') or 0)
    v_total = float(data.get('valor_total') or (qtd * v_unit))
    v_exec = float(data.get('valor_executado') or 0)

    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT INTO cultural_custos (
                projeto_id, rubrica, categoria, unidade, quantidade,
                valor_unitario, valor_total, valor_executado, observacoes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            projeto_id,
            rubrica,
            data.get('categoria') or 'Produção',
            data.get('unidade') or 'Unidade',
            qtd,
            v_unit,
            v_total,
            v_exec,
            data.get('observacoes') or ''
        ))
        conn.commit()
        return jsonify({'status': 'created'}), 201
    finally:
        conn.close()


@app.route('/api/cultural/custos/<int:custo_id>', methods=['DELETE'])
@admin_required
def delete_custo_cultural(custo_id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM cultural_custos WHERE id = ?', (custo_id,))
        conn.commit()
        return jsonify({'status': 'deleted'})
    finally:
        conn.close()


# --- Upload de Documentos (Localhost) e Prestação de Contas ---

@app.route('/api/cultural/projetos/<int:projeto_id>/documentos', methods=['POST'])
@admin_required
def add_documento_cultural(projeto_id):
    etapa = (request.form.get('etapa') or 'habilitacao').strip()
    tipo_documento = (request.form.get('tipo_documento') or '').strip()
    titulo = (request.form.get('titulo') or '').strip()
    link_url = (request.form.get('link_url') or '').strip()
    valor_comprovante = float(request.form.get('valor_comprovante') or 0)
    data_documento = request.form.get('data_documento') or ''
    criado_em = datetime.now().strftime('%Y-%m-%d %H:%M')

    if not tipo_documento or not titulo:
        return jsonify({'error': 'Informe o tipo e o título do documento/comprovante'}), 400

    arquivo = request.files.get('arquivo')
    arquivo_nome, arquivo_path = _salvar_arquivo_local(arquivo, subpasta=f'projeto_{projeto_id}/{etapa}')

    if not arquivo_path and not link_url:
        return jsonify({'error': 'Anexe um arquivo local ou informe um link válido'}), 400

    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT INTO cultural_documentos (
                projeto_id, etapa, tipo_documento, titulo, arquivo_nome,
                arquivo_path, link_url, valor_comprovante, data_documento, criado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            projeto_id, etapa, tipo_documento, titulo, arquivo_nome,
            arquivo_path, link_url, valor_comprovante, data_documento, criado_em
        ))
        conn.commit()
        return jsonify({'status': 'created'}), 201
    finally:
        conn.close()


@app.route('/api/cultural/documentos/<int:doc_id>', methods=['DELETE'])
@admin_required
def delete_documento_cultural(doc_id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM cultural_documentos WHERE id = ?', (doc_id,))
        conn.commit()
        return jsonify({'status': 'deleted'})
    finally:
        conn.close()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)