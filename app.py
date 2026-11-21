import os
import sqlite3
from flask import Flask, render_template, request, jsonify

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

app = Flask(__name__)
app.jinja_options['variable_start_string'] = '[['
app.jinja_options['variable_end_string'] = ']]'

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
DB_PATH = os.path.join(os.path.dirname(__file__), 'barbosa_licitacoes.db')


def is_postgres():
    return bool(DATABASE_URL)


class DBConnection:
    def __init__(self, raw_conn, use_pg):
        self.raw = raw_conn
        self.use_pg = use_pg

    def _sql(self, query):
        return query.replace('?', '%s') if self.use_pg else query

    def execute(self, query, params=()):
        cur = self.raw.cursor()
        cur.execute(self._sql(query), params)
        return cur

    def commit(self):
        self.raw.commit()

    def close(self):
        self.raw.close()


def get_db_connection():
    if is_postgres():
        if psycopg2 is None:
            raise RuntimeError("psycopg2-binary não está instalado.")
        url = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        return DBConnection(conn, use_pg=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return DBConnection(conn, use_pg=False)


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
            conn.raw.rollback()


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
        conn.commit()
        _garantir_colunas_licitacoes(conn)
    finally:
        conn.close()


init_db()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/clientes', methods=['GET', 'POST'])
def handle_clientes():
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
def delete_cliente(id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM clientes WHERE id = ?', (id,))
        conn.commit()
        return jsonify({'status': 'deleted'})
    finally:
        conn.close()


@app.route('/api/licitacoes', methods=['GET', 'POST'])
def handle_licitacoes():
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