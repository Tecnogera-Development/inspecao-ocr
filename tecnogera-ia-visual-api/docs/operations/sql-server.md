# SQL Server do Sisloc (`dbsisloc_tecnogera`) — acesso somente leitura

Como a API lê dados de checklist direto da base do ERP Sisloc, e o que fazer
quando para de funcionar.

Decisão de driver: `docs/exploracao/sql-server-driver.md`.

---

## 1. Coordenadas

| Item | Valor |
|---|---|
| Servidor | Microsoft SQL Server 2017 (RTM-CU31) 14.0.3456.2, Standard Edition, Windows Server 2019 |
| Endereço | `10.246.0.15`, porta TCP `1433`, instância default |
| Banco | `dbsisloc_tecnogera` |
| Usuário | `maisacesso_read` — autenticação SQL, **read-only** |
| Objeto lido hoje | `[dbo].[checklist_produto]` — é uma **VIEW**, 16 colunas |
| Driver no container | `msodbcsql18` + `unixodbc` (instalados no estágio `runtime` do `Dockerfile`) |
| Biblioteca Python | `pyodbc==5.3.0`, via dialeto `mssql+pyodbc` do SQLAlchemy |

### Quem provisiona

A credencial e a liberação de rede são da **TI da Tecnogera (Edelmar)**. O time IA
Visual não cria nem rotaciona esse usuário — pedidos de novo acesso, troca de senha
ou permissão em objeto adicional passam por lá.

### Onde ficam as credenciais

No `env.tecnogera` (arquivo **gitignored** — `.gitignore:61`), nas variáveis:

```dotenv
SISLOC_DB_HOST=10.246.0.15
SISLOC_DB_PORT=1433
SISLOC_DB_NAME=dbsisloc_tecnogera
SISLOC_DB_USER=maisacesso_read
SISLOC_DB_PASSWORD=***
# Opcionais — permitem mudar a postura de TLS sem rebuild:
SISLOC_DB_ENCRYPT=yes
SISLOC_DB_TRUST_SERVER_CERTIFICATE=true
```

Sem `SISLOC_DB_HOST` / `SISLOC_DB_USER` / `SISLOC_DB_PASSWORD` a integração fica
**desligada**: a API sobe normalmente e só quem chamar o Sisloc recebe erro. Isso é
intencional — o ERP não é dependência de boot.

A senha nunca vai para log: `SecretStr` protege o `repr` do `Settings` e o
`app/services/sisloc.py` redige `PWD=` de qualquer mensagem antes de logar ou
levantar exceção. Para diagnóstico use `Settings.sisloc_destino`
(`host:porta/banco`), nunca a connection string.

---

## 2. VPN é pré-requisito, não detalhe

**A rota para `10.246.0.15` não existe fora da VPN da Tecnogera.**

Isso vale tanto para o laptop quanto para a VM `tng-brsdtcapp01`: o container
precisa herdar a rota do host. Sem VPN nada conecta — e o sintoma **parece
credencial errada**:

```
HYT00 Login timeout expired
08001 TCP Provider: Error code 0x274C
```

Regra prática: **`HYT00` = VPN caída.** Antes de mexer em senha, confirme a rota.

---

## 3. Como verificar (o comando)

Dentro do container ou no venv local, com o ambiente carregado:

```bash
python -m app.cli sisloc_ping            # primeiras 12 colunas
python -m app.cli sisloc_ping --verbose  # todas
```

Sai com código `0` em sucesso e `1` em qualquer falha, então serve de check em
script de deploy. Executa apenas um `SELECT TOP (1)` — nada é escrito.

Saída esperada:

```
Sisloc: 10.246.0.15:1433/dbsisloc_tecnogera
OK — conexão estabelecida, 16 colunas
  filial = 'SP - SBC'
  tipo_checklist = 'CHECKLIST OPERACIONAL'
  ...
```

No container:

```bash
docker compose exec api python -m app.cli sisloc_ping
```

O ping **não** está no `/health`: o `HEALTHCHECK` do Docker roda a cada 10 s e
bater no ERP nessa frequência é a maneira mais rápida de o DBA revogar a
credencial. A verificação é sob demanda, por CLI.

### Verificar só a rota, sem Python

O estágio `dev` da imagem inclui `mssql-tools18`:

```bash
export SQLCMDPASSWORD='...'
sqlcmd -S 10.246.0.15,1433 -U maisacesso_read -d dbsisloc_tecnogera -C -l 15 \
       -Q "SELECT @@VERSION"
```

`-C` é o equivalente de `TrustServerCertificate=yes` (ver §4).

---

## 4. TLS: por que `TrustServerCertificate=yes`

O certificado do servidor **não é confiável pela cadeia padrão**, e o
`msodbcsql18` mudou o default de `Encrypt` para `yes`. A combinação exige
`TrustServerCertificate=yes`, senão o handshake falha com:

```
SSL Provider: [error:0A000086:SSL routines::certificate verify failed:self-signed certificate]
```

`Encrypt=strict` **não é opção**: exige TDS 8.0, que é SQL Server 2022+. O
servidor é 2017.

Situação atual: o tráfego vai criptografado (protege contra sniffing passivo), mas
a identidade do servidor não é validada — não protege contra MITM ativo de quem já
esteja dentro do perímetro. **Isto é dívida registrada, não decisão final.** O
alvo é pedir o certificado da CA interna à Tecnogera, colocá-lo na imagem
(`update-ca-certificates`) e virar `SISLOC_DB_TRUST_SERVER_CERTIFICATE=false` —
sem mudar uma linha de Python.

---

## 5. Regras de uso

- **Somente leitura, sempre.** Nenhum `INSERT`/`UPDATE`/`DELETE`/DDL, nem para
  testar. A credencial é read-only no servidor; o código também é.
- **Nenhuma migration aponta para este banco.** O Engine do Sisloc
  (`app/db/sisloc.py`) é separado do Postgres e nunca entra no `MetaData` do
  Alembic. Nenhum modelo ORM é mapeado sobre a base do ERP.
- **Pool pequeno de propósito**: `pool_size=2`, `max_overflow=3`. O gargalo do
  checklist é o LLM, não o Sisloc; abrir dezenas de sessões num ERP de produção
  irrita o DBA com razão.
- **`AUTOCOMMIT`**: leitura em ERP não pode deixar transação aberta segurando
  versão/lock (`sleeping / open transaction`).
- **No worker Arq (async), chamar via `asyncio.to_thread`.** O dialeto é síncrono e
  o Sisloc está atrás de VPN — bloquear o event loop por centenas de ms é real.

---

## 6. Diagnóstico rápido

| Sintoma | Causa | O que fazer |
|---|---|---|
| `HYT00 Login timeout expired` / `08001 ... 0x274C` | VPN caída / rota ausente | Reconectar a VPN; na VM, conferir se o container herda a rota |
| `SSL Provider: certificate verify failed` | `TrustServerCertificate` desligado | `SISLOC_DB_TRUST_SERVER_CERTIFICATE=true` (ou instalar a CA interna) |
| `ImportError: libodbc.so.2` | imagem sem unixODBC | rebuild — o bloco `apt-get` do estágio `runtime` está faltando |
| `Can't open lib 'ODBC Driver 18 for SQL Server' : file not found` | unixODBC presente, `msodbcsql18` ausente/não registrado | conferir `odbcinst -q -d` dentro do container |
| `configuration_error: credenciais do Sisloc ausentes` | `SISLOC_DB_*` não carregadas no processo | conferir se o `env.tecnogera` foi passado ao container |
| `42S02 Invalid object name` | nome de objeto errado | é `checklist_produto` com **underscore**, e é VIEW |
| `08S01 Communication link failure` | conexão ociosa morta por firewall/NAT | já mitigado por `pool_pre_ping` + `pool_recycle=1800`; se persistir, baixar o recycle |
| build trava no `apt-get install msodbcsql18` | falta `ACCEPT_EULA=Y` | já está no `Dockerfile`; se a build for offline, ver a variante air-gapped em `docs/exploracao/sql-server-driver.md` §3 |

---

## 7. Pendências conhecidas

- **Validação a partir do container na VM `tng-brsdtcapp01`.** O ping foi provado do
  laptop com VPN; falta rodar depois do próximo deploy. É pré-requisito de deploy,
  não de código.
- **Certificado da CA interna** (§4) — enquanto não vier, seguimos em
  `TrustServerCertificate=yes`.
- **Lista explícita de colunas.** O ping usa `SELECT *` de propósito; a query de
  produção deve nomear as colunas assim que o dicionário de campos existir
  (`SELECT *` em objeto de ERP é convite a quebra silenciosa).
