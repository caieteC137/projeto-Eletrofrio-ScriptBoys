-- Tabela principal de notificações
CREATE TABLE notificacoes_enviadas (
    id BIGSERIAL PRIMARY KEY,
    alarmeId INTEGER,
    lojaId INTEGER,
    telefone VARCHAR(20),
    criticidade VARCHAR(50),
    mensagem TEXT,
    status VARCHAR(20) DEFAULT 'pendente',
    tentativas INTEGER DEFAULT 0,
    max_tentativas INTEGER DEFAULT 3,
    resposta_api TEXT,
    erro_mensagem TEXT,
    alarmeDhCad TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    proxima_tentativa TIMESTAMP
);

-- Índices para melhor performance
CREATE INDEX idx_notif_alarmeid ON notificacoes_enviadas(alarmeId);
CREATE INDEX idx_notif_lojaid ON notificacoes_enviadas(lojaId);
CREATE INDEX idx_notif_status ON notificacoes_enviadas(status);
CREATE INDEX idx_notif_created ON notificacoes_enviadas(created_at);
CREATE INDEX idx_notif_retry ON notificacoes_enviadas(proxima_tentativa);