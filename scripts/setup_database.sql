-- SQL para criar tabela de notificações no Supabase
-- Execute essas queries no Console do Supabase

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

-- Tabela de logs (opcional, para auditoria completa)
CREATE TABLE logs_notificacao (
    id BIGSERIAL PRIMARY KEY,
    notificacao_id BIGINT REFERENCES notificacoes_enviadas(id) ON DELETE CASCADE,
    status VARCHAR(50),
    resposta_api TEXT,
    mensagem_erro TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_logs_notif ON logs_notificacao(notificacao_id);

-- Função para atualizar updated_at automaticamente
CREATE OR REPLACE FUNCTION update_notificacoes_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger para atualizar updated_at
DROP TRIGGER IF EXISTS trigger_notificacoes_updated ON notificacoes_enviadas;
CREATE TRIGGER trigger_notificacoes_updated
BEFORE UPDATE ON notificacoes_enviadas
FOR EACH ROW
EXECUTE FUNCTION update_notificacoes_updated_at();
