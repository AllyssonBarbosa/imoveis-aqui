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
| `core` | Infraestrutura compartilhada: `Endereco` (com latitude/longitude, usado por Empresa e Imóvel), validadores (imagem, CPF/CNPJ) e a base do isolamento multitenant (`EmpresaOwnedModel`, `EmpresaScopedQuerySetMixin`, permissões por perfil) |
| `empresas` | `Empresa` — imobiliária ou corretor autônomo (mesma tabela) |
| `contas` | `Usuario` customizado (login por e-mail), perfis administrador/gestor/corretor, login do painel e API de autenticação |
| `proprietarios` | `Proprietario` — dono do imóvel (PF/PJ), sem acesso ao sistema |
| `imoveis` | `Caracteristica` (tabela geral do administrador), `Imovel` (parte comum do cadastro) e `FotoImovel` |

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

### 6. Criar os usuários de teste

```bash
python manage.py seed_demo
```

Cria uma empresa, uma cidade, características e os três usuários de teste (tabela abaixo). Pode rodar de novo sem medo — não duplica nada.

### 7. Rodar os testes

```bash
python manage.py test
```

## Usuários de teste

Criados pelo `seed_demo` (passo 6). Pra quem for testar sem mexer em nada:

| Perfil | E-mail | Senha | Onde entra |
|---|---|---|---|
| Administrador | `admin@imoveisaqui.com` | `TrocarSenha123` | `/admin/` |
| Gestor | `gestor@demo.com` | `SenhaForte123` | `/painel/login/` |
| Corretor | `corretor@demo.com` | `SenhaForte123` | `/painel/login/` |

Todos (exceto o administrador) pertencem à empresa "Imobiliaria Demo". São credenciais só de ambiente local/demonstração — troque-as se for usar em qualquer lugar acessível por outras pessoas.

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
| `/painel/imoveis/` | Lista os imóveis da própria empresa | corretor |
| `/painel/imoveis/novo/` | Cadastra a parte comum do imóvel | corretor |
| `/painel/imoveis/<id>/` | Página do imóvel — endereço, fotos e botão de publicar | corretor |
| `/admin/` | Django Admin — cadastro de empresas, usuários e características | administrador |
| `POST /api/auth/login/` | Login da API — corpo `{ "email", "password" }`, devolve `{ "token" }` | app (gestor/corretor) |
| `GET /api/usuarios/` | Lista os usuários da própria empresa (`Authorization: Token <token>`) | app, autenticado |
| `GET/POST /api/proprietarios/?busca=` | Lista (com busca) e cadastra proprietários da própria empresa | app, corretor |
| `GET/POST /api/imoveis/` | Lista e cadastra imóveis da própria empresa | app, corretor |
| `GET/PUT /api/imoveis/<id>/endereco/` | Lê/grava o endereço do imóvel (cidade, CEP, latitude, longitude) | app, corretor |
| `POST /api/imoveis/<id>/publicar/` | Publica o imóvel — recusa se faltar cidade ou coordenada | app, corretor |
| `GET/POST /api/fotos-imovel/?imovel=<id>` | Lista e envia fotos (várias de uma vez, campo `imagens`) | app, corretor |
| `POST /api/fotos-imovel/<id>/definir_capa/` | Marca uma foto como capa (desmarca a anterior) | app, corretor |
| `GET /api/caracteristicas/` | Lista as características cadastradas pelo administrador | app, corretor |
| `GET /api/publico/corretores/<id>/` | Perfil público do corretor (nome, CRECI, foto, telefone, WhatsApp, e-mail, apresentação) | público — vitrine (site e app) |
| `GET /api/publico/empresas/<id>/` | Perfil público da empresa, com a lista de corretores ativos | público — vitrine (site e app) |

O site público (vitrine com os imóveis publicados) ainda não existe — vem numa próxima história da E2. Por enquanto, "publicado" só muda o campo `publicado` do imóvel; nada consome esse dado publicamente ainda.

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

## Imóvel — regras de negócio

- Nasce como rascunho: dá pra cadastrar a parte comum sem endereço nem fotos e ir completando aos poucos — a natureza específica (residencial, comercial etc.) vem numa próxima história.
- Código único **por empresa** (mesmo padrão do documento do proprietário) — duas empresas podem usar o mesmo código.
- Corretor responsável precisa ser da própria empresa e ter perfil corretor; proprietário também precisa ser da própria empresa — tudo validado no `clean()` do modelo, não só na tela.
- A finalidade decide o preço obrigatório: venda exige preço de venda, aluguel exige valor do aluguel, e "venda e aluguel" exige os dois.
- **Publicar é uma ação separada de salvar**: só funciona se o imóvel tiver endereço com latitude e longitude — sem isso, recusa com mensagem clara e o imóvel continua rascunho.
- Fotos aceitam envio múltiplo numa só requisição, guardam a ordem de chegada e têm no máximo uma marcada como capa (garantido também no banco, não só na aplicação).

## Progresso por etapa

- [x] **E1 (em andamento)** — W01: login, empresa, corretores/gestor e isolamento multitenant na web. W02: cadastro de corretores pelo gestor, ativação/desativação e perfil público (corretor e empresa). W03: proprietários (PF/PJ com CPF/CNPJ validado), características do acervo e confirmação das cidades por empresa. W04: parte comum do imóvel, endereço com coordenadas, fotos com capa e a regra de publicação.
- [ ] E1 — protótipo das telas do app, navegação, localização e escolha da cidade.
- [ ] E2 — Web: as quatro naturezas do imóvel, loteamento (quadras/lotes), site público. App: vitrine com filtros, página do imóvel, WhatsApp, favoritos.
- [ ] E3 — Web: contrato, parcelas, baixa com comprovante, relatórios. App: área do corretor, área do gestor, apresentação final.

> Este README é atualizado conforme novas histórias forem implementadas.
