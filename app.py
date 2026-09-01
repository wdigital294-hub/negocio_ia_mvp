from flask import Flask, request, redirect, url_for, session, render_template_string
import sqlite3
from pathlib import Path
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

# ============================================================
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)

app.secret_key = "NEGOCIO_IA_CHAVE_SECRETA_2026"

DB = Path(__file__).with_name("negocio.db")


# ============================================================
# BANCO DE DADOS
# ============================================================

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def coluna_existe(conn, tabela, coluna):
    colunas = conn.execute(f"PRAGMA table_info({tabela})").fetchall()
    return any(c["name"] == coluna for c in colunas)


def init_db():
    conn = db()

    # --------------------------------------------------------
    # VENDEDORES
    # --------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sellers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --------------------------------------------------------
    # PRIMEIRO VENDEDOR
    #
    # Se o banco antigo já tiver produtos, eles serão ligados
    # a este primeiro vendedor.
    # --------------------------------------------------------

    primeiro = conn.execute(
        "SELECT id FROM sellers ORDER BY id LIMIT 1"
    ).fetchone()

    if not primeiro:
        conn.execute("""
            INSERT INTO sellers (username, password_hash)
            VALUES (?, ?)
        """, (
            "vendedor1",
            generate_password_hash("123456")
        ))

    vendedor_principal = conn.execute(
        "SELECT id FROM sellers ORDER BY id LIMIT 1"
    ).fetchone()["id"]

    # --------------------------------------------------------
    # PRODUTOS
    # --------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            stock INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (seller_id) REFERENCES sellers(id)
        )
    """)

    # Se o banco antigo já tinha products sem seller_id,
    # adicionamos a coluna.
    if not coluna_existe(conn, "products", "seller_id"):
        conn.execute("""
            ALTER TABLE products
            ADD COLUMN seller_id INTEGER NOT NULL DEFAULT 1
        """)

    # --------------------------------------------------------
    # VENDAS
    # --------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            total REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# LOGIN
# ============================================================

def vendedor_logado():
    return session.get("seller_id")


def login_obrigatorio(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not vendedor_logado():
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():

    erro = None

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = db()

        vendedor = conn.execute("""
            SELECT *
            FROM sellers
            WHERE username = ?
        """, (username,)).fetchone()

        conn.close()

        if vendedor and check_password_hash(
            vendedor["password_hash"],
            password
        ):
            session.clear()
            session["seller_id"] = vendedor["id"]
            session["username"] = vendedor["username"]

            return redirect(url_for("dashboard"))

        erro = "Utilizador ou palavra-passe incorretos."

    return render_template_string(LOGIN_HTML, erro=erro)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# CRIAR CONTA DE VENDEDOR
# ============================================================

@app.route("/criar-conta", methods=["GET", "POST"])
def criar_conta():

    erro = None

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if len(username) < 3:
            erro = "O nome do vendedor deve ter pelo menos 3 caracteres."

        elif len(password) < 6:
            erro = "A palavra-passe deve ter pelo menos 6 caracteres."

        else:
            conn = db()

            existente = conn.execute("""
                SELECT id
                FROM sellers
                WHERE username = ?
            """, (username,)).fetchone()

            if existente:
                erro = "Esse vendedor já existe."
                conn.close()

            else:
                conn.execute("""
                    INSERT INTO sellers (username, password_hash)
                    VALUES (?, ?)
                """, (
                    username,
                    generate_password_hash(password)
                ))

                conn.commit()
                conn.close()

                return redirect(url_for("login"))

    return render_template_string(REGISTER_HTML, erro=erro)


# ============================================================
# PAINEL
# ============================================================

@app.route("/")
@login_obrigatorio
def dashboard():

    seller_id = vendedor_logado()

    conn = db()

    # --------------------------------------------------------
    # PRODUTOS: SOMENTE DO VENDEDOR LOGADO
    # --------------------------------------------------------

    products = conn.execute("""
        SELECT *
        FROM products
        WHERE seller_id = ?
        ORDER BY id DESC
    """, (seller_id,)).fetchall()

    # --------------------------------------------------------
    # VENDAS: SOMENTE DO VENDEDOR LOGADO
    # --------------------------------------------------------

    sales = conn.execute("""
        SELECT
            sales.id,
            sales.quantity,
            sales.total,
            sales.created_at,
            products.name
        FROM sales
        INNER JOIN products
            ON products.id = sales.product_id
        WHERE products.seller_id = ?
        ORDER BY sales.id DESC
        LIMIT 20
    """, (seller_id,)).fetchall()

    # --------------------------------------------------------
    # TOTAIS DO VENDEDOR
    # --------------------------------------------------------

    totals = conn.execute("""
        SELECT
            COALESCE(SUM(sales.total), 0) AS revenue,
            COALESCE(SUM(sales.quantity), 0) AS units
        FROM sales
        INNER JOIN products
            ON products.id = sales.product_id
        WHERE products.seller_id = ?
    """, (seller_id,)).fetchone()

    total_produtos = conn.execute("""
        SELECT COUNT(*) AS total
        FROM products
        WHERE seller_id = ?
    """, (seller_id,)).fetchone()["total"]

    conn.close()

    return render_template_string(
        DASHBOARD_HTML,
        products=products,
        sales=sales,
        totals=totals,
        total_produtos=total_produtos,
        username=session.get("username")
    )


# ============================================================
# ADICIONAR PRODUTO
# ============================================================

@app.post("/products")
@login_obrigatorio
def add_product():

    seller_id = vendedor_logado()

    name = request.form.get("name", "").strip()

    try:
        price = float(request.form.get("price", 0))
        quantity = int(request.form.get("stock", 0))
    except ValueError:
        return redirect(url_for("dashboard"))

    if not name or price < 0 or quantity < 0:
        return redirect(url_for("dashboard"))

    conn = db()

    # --------------------------------------------------------
    # PROCURA PRODUTO DO MESMO VENDEDOR
    #
    # "Perfume", "perfume" e " PERFUME "
    # serão considerados o mesmo produto.
    # --------------------------------------------------------

    product = conn.execute("""
        SELECT *
        FROM products
        WHERE seller_id = ?
          AND LOWER(TRIM(name)) = LOWER(TRIM(?))
        LIMIT 1
    """, (seller_id, name)).fetchone()

    if product:

        # Produto já existe:
        # acrescenta a quantidade ao stock existente.
        conn.execute("""
            UPDATE products
            SET stock = stock + ?,
                price = ?
            WHERE id = ?
              AND seller_id = ?
        """, (
            quantity,
            price,
            product["id"],
            seller_id
        ))

    else:

        # Produto novo para este vendedor.
        conn.execute("""
            INSERT INTO products
                (seller_id, name, price, stock)
            VALUES (?, ?, ?, ?)
        """, (
            seller_id,
            name,
            price,
            quantity
        ))

    conn.commit()
    conn.close()

    return redirect(url_for("dashboard"))


# ============================================================
# VENDER
# ============================================================

@app.post("/sell/<int:product_id>")
@login_obrigatorio
def sell(product_id):

    seller_id = vendedor_logado()

    try:
        quantity = int(request.form.get("quantity", 0))
    except ValueError:
        quantity = 0

    if quantity < 1:
        return redirect(url_for("dashboard"))

    conn = db()

    # IMPORTANTE:
    # procura o produto E verifica o seller_id.
    # Um vendedor nunca consegue vender o produto de outro.
    product = conn.execute("""
        SELECT *
        FROM products
        WHERE id = ?
          AND seller_id = ?
    """, (
        product_id,
        seller_id
    )).fetchone()

    if product and product["stock"] >= quantity:

        total = product["price"] * quantity

        conn.execute("""
            UPDATE products
            SET stock = stock - ?
            WHERE id = ?
              AND seller_id = ?
        """, (
            quantity,
            product_id,
            seller_id
        ))

        conn.execute("""
            INSERT INTO sales
                (product_id, quantity, total)
            VALUES (?, ?, ?)
        """, (
            product_id,
            quantity,
            total
        ))

        conn.commit()

    conn.close()

    return redirect(url_for("dashboard"))


# ============================================================
# ELIMINAR UM PRODUTO
# ============================================================

@app.post("/delete-product/<int:product_id>")
@login_obrigatorio
def delete_product(product_id):

    seller_id = vendedor_logado()

    conn = db()

    # Primeiro elimina as vendas relacionadas ao produto.
    # Assim o SQLite não bloqueia a eliminação por causa da
    # chave estrangeira.
    conn.execute("""
        DELETE FROM sales
        WHERE product_id = ?
          AND EXISTS (
              SELECT 1
              FROM products
              WHERE products.id = sales.product_id
                AND products.seller_id = ?
          )
    """, (
        product_id,
        seller_id
    ))

    # Depois elimina SOMENTE o produto do vendedor logado.
    conn.execute("""
        DELETE FROM products
        WHERE id = ?
          AND seller_id = ?
    """, (
        product_id,
        seller_id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("dashboard"))


# ============================================================
# APAGAR TODOS OS PRODUTOS DO VENDEDOR
# ============================================================

@app.post("/clear-all")
@login_obrigatorio
def clear_all():

    seller_id = vendedor_logado()

    conn = db()

    # Primeiro elimina somente as vendas dos produtos
    # pertencentes ao vendedor logado.
    conn.execute("""
        DELETE FROM sales
        WHERE product_id IN (
            SELECT id
            FROM products
            WHERE seller_id = ?
        )
    """, (seller_id,))

    # Depois elimina somente os produtos desse vendedor.
    conn.execute("""
        DELETE FROM products
        WHERE seller_id = ?
    """, (seller_id,))

    conn.commit()
    conn.close()

    return redirect(url_for("dashboard"))


# ============================================================
# PÁGINA DE LOGIN
# ============================================================

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>Negócio IA — Login</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f3f4f6;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
}

.login-box {
    width: 390px;
    background: white;
    padding: 35px;
    border-radius: 15px;
    box-shadow: 0 5px 25px rgba(0,0,0,0.10);
}

h1 {
    margin-top: 0;
    text-align: center;
}

p.subtitulo {
    text-align: center;
    color: #666;
    margin-bottom: 30px;
}

input {
    width: 100%;
    padding: 14px;
    margin-bottom: 15px;
    border: 1px solid #bbb;
    font-size: 16px;
    border-radius: 6px;
}

button {
    width: 100%;
    padding: 14px;
    border: 0;
    border-radius: 6px;
    background: #111827;
    color: white;
    font-size: 16px;
    cursor: pointer;
}

button:hover {
    opacity: 0.9;
}

a {
    display: block;
    text-align: center;
    margin-top: 20px;
    color: #2563eb;
    text-decoration: none;
}

.erro {
    background: #fee2e2;
    color: #991b1b;
    padding: 12px;
    border-radius: 6px;
    margin-bottom: 15px;
}

</style>
</head>

<body>

<div class="login-box">

<h1>Negócio IA</h1>

<p class="subtitulo">Gestor de vendas e estoque</p>

{% if erro %}
<div class="erro">{{ erro }}</div>
{% endif %}

<form method="POST">

<input
    type="text"
    name="username"
    placeholder="Nome do vendedor"
    required
>

<input
    type="password"
    name="password"
    placeholder="Palavra-passe"
    required
>

<button type="submit">
    Entrar
</button>

</form>

<a href="{{ url_for('criar_conta') }}">
    Criar conta de vendedor
</a>

</div>

</body>
</html>
"""


# ============================================================
# PÁGINA DE CRIAR CONTA
# ============================================================

REGISTER_HTML = """
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>Criar vendedor</title>

<style>

body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f3f4f6;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
}

.box {
    width: 390px;
    background: white;
    padding: 35px;
    border-radius: 15px;
    box-shadow: 0 5px 25px rgba(0,0,0,0.10);
}

h1 {
    margin-top: 0;
}

input {
    width: 100%;
    padding: 14px;
    margin-bottom: 15px;
    border: 1px solid #bbb;
    border-radius: 6px;
    box-sizing: border-box;
}

button {
    width: 100%;
    padding: 14px;
    border: 0;
    border-radius: 6px;
    background: #111827;
    color: white;
    font-size: 16px;
    cursor: pointer;
}

a {
    display: block;
    text-align: center;
    margin-top: 20px;
    text-decoration: none;
    color: #2563eb;
}

.erro {
    background: #fee2e2;
    color: #991b1b;
    padding: 12px;
    border-radius: 6px;
    margin-bottom: 15px;
}

</style>
</head>

<body>

<div class="box">

<h1>Criar vendedor</h1>

{% if erro %}
<div class="erro">{{ erro }}</div>
{% endif %}

<form method="POST">

<input
    type="text"
    name="username"
    placeholder="Nome do vendedor"
    required
>

<input
    type="password"
    name="password"
    placeholder="Palavra-passe"
    required
>

<button type="submit">
    Criar conta
</button>

</form>

<a href="{{ url_for('login') }}">
    Voltar para o login
</a>

</div>

</body>
</html>
"""


# ============================================================
# PAINEL PRINCIPAL
# ============================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pt">
<head>

<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>Negócio IA — Painel</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f3f4f6;
    color: #111;
}

.container {
    width: 92%;
    max-width: 1250px;
    margin: 30px auto;
}

.topo {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 25px;
}

.topo h1 {
    margin: 0;
}

.usuario {
    display: flex;
    align-items: center;
    gap: 15px;
}

.logout {
    background: #374151;
    color: white;
    padding: 10px 15px;
    border-radius: 6px;
    text-decoration: none;
}

.cards {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 18px;
    margin-bottom: 25px;
}

.card {
    background: white;
    padding: 25px;
    border-radius: 15px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}

.card strong {
    display: block;
    font-size: 28px;
    margin-top: 12px;
}

.secao {
    background: white;
    padding: 25px;
    border-radius: 15px;
    margin-bottom: 25px;
}

.form-produto {
    display: grid;
    grid-template-columns: 1.5fr 1fr 1fr auto;
    gap: 15px;
}

input {
    padding: 13px;
    border: 1px solid #aaa;
    font-size: 16px;
}

button {
    padding: 12px 18px;
    border: 0;
    border-radius: 5px;
    cursor: pointer;
    font-size: 15px;
}

.guardar {
    background: #111827;
    color: white;
}

.apagar-todos {
    background: #dc2626;
    color: white;
    float: right;
}

table {
    width: 100%;
    border-collapse: collapse;
}

th,
td {
    padding: 15px 12px;
    border-bottom: 1px solid #ddd;
    text-align: left;
}

th {
    font-weight: bold;
}

.venda {
    display: flex;
    gap: 8px;
    align-items: center;
}

.venda input {
    width: 90px;
}

.vender {
    background: #eee;
}

.eliminar {
    background: #dc2626;
    color: white;
}

.estoque-zero {
    color: #dc2626;
    font-weight: bold;
}

.limpar {
    clear: both;
}

@media (max-width: 800px) {

    .cards {
        grid-template-columns: 1fr;
    }

    .form-produto {
        grid-template-columns: 1fr;
    }

    table {
        font-size: 14px;
    }

    .secao {
        overflow-x: auto;
    }

    .topo {
        flex-direction: column;
        align-items: flex-start;
        gap: 15px;
    }
}

</style>

</head>

<body>

<div class="container">

<div class="topo">

<h1>Negócio IA — Painel</h1>

<div class="usuario">

<span>
Vendedor: <strong>{{ username }}</strong>
</span>

<a class="logout" href="{{ url_for('logout') }}">
Sair
</a>

</div>

</div>


<!-- =====================================================
     RESUMO
====================================================== -->

<div class="cards">

<div class="card">

Receita

<strong>
{{ "%.2f"|format(totals["revenue"]) }} Kz
</strong>

</div>

<div class="card">

Unidades vendidas

<strong>
{{ totals["units"] }}
</strong>

</div>

<div class="card">

Produtos

<strong>
{{ total_produtos }}
</strong>

</div>

</div>


<!-- =====================================================
     ADICIONAR PRODUTO
====================================================== -->

<div class="secao">

<h2>Adicionar produto</h2>

<form
    method="POST"
    action="{{ url_for('add_product') }}"
    class="form-produto"
>

<input
    type="text"
    name="name"
    placeholder="Nome do produto"
    required
>

<input
    type="number"
    name="price"
    placeholder="Preço (Kz)"
    min="0"
    step="0.01"
    required
>

<input
    type="number"
    name="stock"
    placeholder="Quantidade"
    min="0"
    required
>

<button class="guardar" type="submit">
Guardar
</button>

</form>

</div>


<!-- =====================================================
     STOCK
====================================================== -->

<div class="secao">

<h2>Stock</h2>

<form
    method="POST"
    action="{{ url_for('clear_all') }}"
    onsubmit="return confirm(
        'ATENÇÃO: isto vai apagar TODOS os seus produtos e vendas relacionadas. Continuar?'
    );"
>

<button
    type="submit"
    class="apagar-todos"
>
APAGAR TODOS OS PRODUTOS
</button>

</form>

<div class="limpar"></div>

<br>

<table>

<thead>

<tr>

<th>Produto</th>

<th>Preço</th>

<th>Stock</th>

<th>Venda</th>

<th>Ações</th>

</tr>

</thead>

<tbody>

{% for product in products %}

<tr>

<td>
{{ product["name"] }}
</td>

<td>
{{ "%.2f"|format(product["price"]) }} Kz
</td>

<td>

{% if product["stock"] == 0 %}

<span class="estoque-zero">
0
</span>

{% else %}

{{ product["stock"] }}

{% endif %}

</td>

<td>

<form
    method="POST"
    action="{{ url_for('sell', product_id=product['id']) }}"
    class="venda"
>

<input
    type="number"
    name="quantity"
    value="1"
    min="1"
    max="{{ product['stock'] }}"
    required
>

<button
    type="submit"
    class="vender"
    {% if product["stock"] == 0 %}disabled{% endif %}
>
Vender
</button>

</form>

</td>

<td>

<form
    method="POST"
    action="{{ url_for('delete_product', product_id=product['id']) }}"
    onsubmit="return confirm(
        'Tem certeza que deseja eliminar este produto?'
    );"
>

<button
    type="submit"
    class="eliminar"
>
Eliminar
</button>

</form>

</td>

</tr>

{% else %}

<tr>

<td colspan="5">
Nenhum produto cadastrado.
</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>


<!-- =====================================================
     ÚLTIMAS VENDAS
====================================================== -->

<div class="secao">

<h2>Últimas vendas</h2>

<table>

<thead>

<tr>

<th>Produto</th>

<th>Quantidade</th>

<th>Total</th>

<th>Data</th>

</tr>

</thead>

<tbody>

{% for sale in sales %}

<tr>

<td>
{{ sale["name"] }}
</td>

<td>
{{ sale["quantity"] }}
</td>

<td>
{{ "%.2f"|format(sale["total"]) }} Kz
</td>

<td>
{{ sale["created_at"] }}
</td>

</tr>

{% else %}

<tr>

<td colspan="4">
Ainda não existem vendas.
</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>

</div>

</body>
</html>
"""


# ============================================================
# INICIAR
# ============================================================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )