from flask import Flask, render_template_string
from test2 import Telemetria, SessionLocal

app = Flask(__name__)


@app.route('/')
def index():
    with SessionLocal() as session:
        registros = session.query(Telemetria).limit(100).all()

    html = """
    <html>
    <head>
      <title>Telemetria Dashboard</title>
      <style>
        table { border-collapse: collapse; width: 100%; }
        th, td { padding: 8px; border: 1px solid #ccc; text-align: left; }
        pre { margin: 0; white-space: pre-wrap; word-break: break-word; }
      </style>
    </head>
    <body>
    <h1>Dados de Telemetria</h1>
    <table>
    <tr><th>ID</th><th>Dispositivo ID</th><th>Hora</th><th>Dados</th></tr>
    {% for reg in registros %}
    <tr>
      <td>{{ reg.id }}</td>
      <td>{{ reg.dispositivo_id }}</td>
      <td>{{ reg.hora }}</td>
      <td><pre>{{ reg.dados }}</pre></td>
    </tr>
    {% endfor %}
    </table>
    </body>
    </html>
    """
    return render_template_string(html, registros=registros)


if __name__ == '__main__':
    app.run(debug=True)
