# Imóveis Aqui

Marketplace de imóveis com duas frentes sobre o mesmo banco de dados:

- **Web** (`Web/`) — Django + Django REST Framework. Site público (vitrine), painel de gestão (login) e a API consumida pelo app.
- **App** (`App/`) — Flutter. Vitrine sem login + áreas de corretor e gestor atrás do login. *(ainda não iniciado)*

O app é uma tela sobre os dados: nenhuma regra de negócio é decidida nele — tudo é decidido e calculado no Django.

## Stack

- Python 3.11 + Django 5.2 + Django REST Framework
- PostgreSQL
- Flutter (Dart) — App

## Estrutura do backend (`Web/`)

| App | Responsabilidade |
|---|---|
| `localizacao` | `Cidade` — tabela geral mantida pelo administrador, usada na vitrine por cidade |
| `core` | Infraestrutura compartilhada: `Endereco` (reaproveitado por Empresa e, depois, Imóvel), validadores (imagem, CPF/CNPJ) e a base do isolamento multitenant (`EmpresaOwnedModel`, `EmpresaScopedQuerySetMixin`, `EmpresaRequiredMixin`) |
| `empresas` | `Empresa` — imobiliária ou corretor autônomo (mesma tabela) |
| `contas` | `Usuario` customizado (login por e-mail), perfis administrador/gestor/corretor, login do painel e API de autenticação |
| `proprietarios` | `Proprietario` — dono do imóvel (PF/PJ), sem acesso ao sistema |
| `imoveis` | `Caracteristica` — tabela geral mantida pelo administrador (o `Imóvel` da E2 vai morar aqui também) |

### Isolamento multitenant

Toda tabela do acervo (imóvel, contrato etc.) deve herdar de `core.models.EmpresaOwnedModel`, e todo ViewSet que expõe esse dado deve usar `core.mixins.EmpresaScopedQuerySetMixin`, que filtra o `queryset` pela empresa do usuário logado. Isso garante que trocar o ID na URL não abre dado de outra empresa (cai em 404, sem revelar que o registro existe). Essa regra não pode ser quebrada — é o critério de maior peso na avaliação.

## Como rodar o backend

### 1. Pré-requisitos

- Python 3.11+
- PostgreSQL rodando localmente

### 2. Ambiente virtual e dependências

```bash
cd Web
python -m venv venv
```

PowerShell:
```powershell
.\venv\Scripts\Activate.ps1
```

Git Bash:
```bash
source venv/Scripts/activate
```

```bash
pip install -r requirements.txt
```

### 3. Variáveis de ambiente

Copie `Web/.env.example` para `Web/.env` e ajuste as credenciais do seu PostgreSQL:

```bash
cp .env.example .env
```

O `.env` não vai para o git (está no `.gitignore`) — cada máquina tem o seu.

### 4. Banco de dados

Crie o banco (uma vez só, por máquina):

```sql
CREATE DATABASE imoveis_aqui;
```

Depois aplique as migrations:

```bash
python manage.py migrate
```

### 5. Subir o servidor

```bash
python manage.py runserver
```

Todas as telas e rotas já prontas estão listadas em [Telas prontas](#telas-prontas), logo abaixo.

### 6. Criar o primeiro administrador

```bash
python manage.py shell
```
```python
from contas.models import Usuario
Usuario.objects.create_superuser(email="admin@exemplo.com", senha="uma-senha-forte", nome="Administrador")
```

### 7. Rodar os testes

```bash
python manage.py test
```

## Telas prontas

Com o servidor rodando em `http://127.0.0.1:8000`:

| Rota | O que é | Quem acessa |
|---|---|---|
| `/painel/login/` | Entrar no painel | qualquer usuário (gestor, corretor); administrador usa o `/admin/` |
| `/painel/` | Home do painel (dados do usuário logado) | qualquer usuário logado |
| `/painel/corretores/` | Lista os corretores da própria empresa, com ativar/desativar | gestor |
| `/painel/corretores/novo/` | Cadastra um corretor novo | gestor |
| `/painel/proprietarios/` | Lista os proprietários da própria empresa, com busca por nome ou documento | corretor |
| `/painel/proprietarios/novo/` | Cadastra um proprietário (PF ou PJ) | corretor |
| `/admin/` | Django Admin — cadastro de empresas, usuários e características | administrador |
| `POST /api/auth/login/` | Login da API — corpo `{ "email", "password" }`, devolve `{ "token" }` | app (gestor/corretor) |
| `GET /api/usuarios/` | Lista os usuários da própria empresa (`Authorization: Token <token>`) | app, autenticado |
| `GET/POST /api/proprietarios/?busca=` | Lista (com busca) e cadastra proprietários da própria empresa | app, autenticado |
| `GET /api/publico/corretores/<id>/` | Perfil público do corretor (nome, CRECI, foto, telefone, WhatsApp, e-mail, apresentação) | público — vitrine (site e app) |
| `GET /api/publico/empresas/<id>/` | Perfil público da empresa, com a lista de corretores ativos | público — vitrine (site e app) |

O site público (vitrine, sem login) ainda não existe — é entrega da E2, quando o acervo de imóveis passar a existir.

## Painel do gestor — regras de negócio

- O `perfil` e a `empresa` do corretor cadastrado são sempre definidos pelo servidor, nunca pelo formulário — garante que o gestor só cadastra na própria empresa.
- Ativar/desativar: desativado some da vitrine (API pública) e não recebe contato, mas continua no banco — os imóveis dele (quando existirem, a partir da E2) não são afetados.
- Corretor de imobiliária precisa preencher o próprio CRECI, telefone e WhatsApp. Corretor autônomo pode deixar em branco: o perfil público usa os dados da empresa.
- Foto do corretor e logomarca da empresa passam por validação de tipo (JPG/PNG/WEBP, conferido de verdade com Pillow) e tamanho (até 5MB) antes de gravar.

## Proprietários — regras de negócio

- CPF (pessoa física) e CNPJ (pessoa jurídica) são validados pelo dígito verificador de verdade, não só pelo tamanho — mesma validação usada agora no CNPJ/CPF da própria `Empresa`.
- O documento é guardado só com os dígitos (a pontuação some na hora de salvar); `documento_formatado` devolve com máscara pra exibição.
- Documento repetido só é bloqueado **dentro da mesma empresa** — duas empresas diferentes podem ter o mesmo proprietário cadastrado, cada uma no seu isolamento.
- A busca (`?busca=`, tanto no painel quanto na API) casa tanto pelo nome quanto pelo documento (com ou sem pontuação).
- Proprietário não tem login — é só um cadastro de contato, sem conta de usuário associada.

## Progresso por etapa

- [x] **E1 (em andamento)** — W01: login, empresa, corretores/gestor e isolamento multitenant na web. W02: cadastro de corretores pelo gestor, ativação/desativação e perfil público (corretor e empresa). W03: proprietários (PF/PJ com CPF/CNPJ validado), características do acervo e confirmação das cidades por empresa.
- [ ] E1 — protótipo das telas do app, navegação, localização e escolha da cidade.
- [ ] E2 — Web: imóvel (endereço, fotos, quatro naturezas), loteamento (quadras/lotes), publicação, site público. App: vitrine com filtros, página do imóvel, WhatsApp, favoritos.
- [ ] E3 — Web: contrato, parcelas, baixa com comprovante, relatórios. App: área do corretor, área do gestor, apresentação final.

> Este README é atualizado conforme novas histórias forem implementadas.
