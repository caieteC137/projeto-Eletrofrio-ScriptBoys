-- Tabela de unidades
create table public.unidades (
  id bigint generated always as identity not null,
  "lojaId" text null,
  ativo boolean null,
  "lojaNm" text null,
  "lojaApelido" text null,
  "tpContratoId" text null,
  "tpContratoNm" text null,
  "dtValContrato" text null,
  "contaId" text null,
  "contaNm" text null,
  cnpj text null,
  "nrPedido" text null,
  telefone text null,
  "dhSinalVida" text null,
  "apiTipo" text null,
  endereco text null,
  constraint unidades_pkey primary key (id),
  constraint unidades_lojaId_key unique ("lojaId")
) TABLESPACE pg_default;