# Projeto Eletrofrio Telemetria

Este projeto coleta dados de telemetria de dispositivos via API e armazena em um banco PostgreSQL. Inclui uma interface web simples para visualização.

## Configuração

1. Instale o Docker e inicie o Docker Desktop.

2. Execute o container PostgreSQL:
   ```
   docker-compose up -d
   ```

3. Instale as dependências Python:
   ```
   pip install -r requirements.txt
   ```

4. Execute o script de coleta de dados:
   ```
   python test2.py
   ```

5. Execute a aplicação web:
   ```
   python app.py
   ```

6. Acesse http://localhost:5000 para visualizar os dados.

## Notas

- O banco é PostgreSQL rodando em container.
- Dados são armazenados na tabela 'telemetria' com campos: id, dispositivo_id, hora, dados (JSON).
- A interface mostra os primeiros 100 registros.
- Para coletar todos os dispositivos (1-499), execute test2.py. Pode demorar devido ao sleep de 0.5s entre requests.