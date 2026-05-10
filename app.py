from flask import Flask, render_template_string
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test2 import Telemetria, Session

app = Flask(__name__)


@app.route('/')
def index():
    session = Session()
    registros = session.query(Telemetria).limit(
        100).all()  # Limitar para performance
    session.close()

    html = """
    <html>
    <head><title>Telemetria Dashboard</title></head>
    <body>
    <h1>Dados de Telemetria</h1>
    <table border="1">
    <tr><th>ID</th><th>Dispositivo ID</th><th>Hora</th><th>Dados</th></tr>
    {% for reg in registros %}
    <tr>
    <td>{{ reg.id }}</td>
    <td>{{ reg.dispositivo_id }}</td>
    <td>{{ reg.hora }}</td>
    <td>{{ reg.dados }}</td>
    </tr>
    {% endfor %}
    </table>
    </body>
    </html>
    """
    return render_template_string(html, registros=registros)


if __name__ == '__main__':
    app.run(debug=True)
