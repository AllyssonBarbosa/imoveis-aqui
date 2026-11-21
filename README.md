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
| `clientes` | `Cliente` — quem compra ou aluga (PF/PJ, CPF/CNPJ e contato), sem acesso ao sistema e fora da vitrine (W12) |
| `contratos` | `Contrato` (venda ou locação) e `Parcela`, gerada pelo servidor ao salvar: centavos que sobram na última parcela, vencimento no último dia em mês mais curto, locação com uma parcela por mês da vigência (W12). `DocumentoContrato` guarda os anexos na pasta privada `Web/arquivos_privados/` (fora de `media/`, sem URL; download só pela rota que confere empresa e perfil). `Baixa` registra cada pagamento de parcela com comprovante (também na pasta privada): várias baixas por parcela, parcial ou a mais; a parcela está paga quando a soma cobre o valor. A situação da parcela (paga, a vencer, atrasada — venceu antes de hoje e tem saldo — ou prevista, no rascunho) é calculada na consulta (`Parcela.objects.com_situacao(hoje)`, dia de São Paulo), nunca gravada |
| `relatorios` | Sem tabelas: relatórios do gestor calculados na hora (acervo por natureza × situação, contatos por corretor no período e parcelas em atraso/a vencer), sempre só da empresa dele (W12) |
| `imoveis` | `Caracteristica` (tabela geral do administrador), `Imovel` (parte comum do cadastro) e `FotoImovel` |
| `contatos` | `Contato` — registro de que alguém procurou um imóvel (WhatsApp, telefone ou e-mail) e o retorno anotado (W11) |

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

Os documentos de contrato e os comprovantes de pagamento (W12) ficam em `Web/arquivos_privados/` — fora de `media/`, sem URL pública e também fora do git. Para usar outra pasta, defina `ARQUIVOS_PRIVADOS_ROOT` no `.env` (nunca dentro de `media/`).

Duas variáveis opcionais controlam o limite de taxa das rotas públicas da vitrine (os padrões já funcionam): `VITRINE_TAXA_ANONIMA` (padrão `120/min` por IP) e `NUM_PROXIES` (padrão `0`, usa o IP da conexão; atrás de proxy reverso, o número de proxies confiáveis — senão todos os visitantes dividem o IP do proxy e o mesmo limite). `VITRINE_TAXA_ANONIMA` é validada na partida (`<número>/<período>`, ex. `120/min`); formato inválido para a aplicação com `ImproperlyConfigured`. **Em produção informe `NUM_PROXIES` sempre** (o `.env.example` deixa a linha comentada de propósito): sem a variável fora do `DEBUG`, `python manage.py check --deploy` mostra o aviso `core.W001`. Rode esse comando no checklist de deploy.

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

Cria uma empresa, uma cidade, características, os três usuários de teste (tabela abaixo) e, para a W12, um imóvel, um cliente e um contrato de venda assinado, com 60 parcelas (a 1ª paga e a 2ª paga pela metade). Pode rodar de novo sem medo — não duplica nada.

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
| `/` | Site: início com a escolha da cidade | público, sem login |
| `/cidade/<id>/` | Site: vitrine da cidade, com os mesmos filtros e ordenação da API | público, sem login |
| `/imovel/<id>/` | Site: página do imóvel — galeria, dados da natureza, valores, mapa e WhatsApp | público, sem login |
| `/empresa/<id>/` | Site: perfil da empresa, com os corretores e a vitrine dela | público, sem login |
| `/corretor/<id>/` | Site: perfil do corretor, com a vitrine dele | público, sem login |
| `/painel/login/` | Entrar no painel | qualquer usuário (gestor, corretor); administrador usa o `/admin/` |
| `/painel/` | Home do painel (dados do usuário logado) | qualquer usuário logado |
| `/painel/corretores/` | Lista os corretores da própria empresa, com ativar/desativar | gestor |
| `/painel/corretores/novo/` | Cadastra um corretor novo | gestor |
| `/painel/proprietarios/` | Lista os proprietários da própria empresa, com busca por nome ou documento | corretor |
| `/painel/proprietarios/novo/` | Cadastra um proprietário (PF ou PJ) | corretor |
| `/painel/clientes/?busca=` | Lista os clientes da própria empresa, com busca por nome ou documento | corretor e gestor |
| `/painel/clientes/novo/` e `/painel/clientes/<id>/editar/` | Cadastra e edita cliente (CPF/CNPJ válido e telefone ou e-mail) | corretor e gestor |
| `/painel/contratos/?tipo=&situacao=` | Lista os contratos | gestor (todos da empresa) e corretor (só os dele) |
| `/painel/contratos/novo/` e `/painel/contratos/<id>/editar/` | Cadastra e edita o contrato; ao salvar, gera as parcelas (assinado: só observações) | gestor |
| `/painel/contratos/<id>/` | Dados do contrato e todas as parcelas, com pago, saldo e situação (atrasadas em destaque) | gestor e corretor do contrato |
| `POST /painel/contratos/<id>/documentos/` | Anexa documento ao contrato (PDF, JPG ou PNG até 10MB) | gestor |
| `/painel/contratos/<id>/documentos/<doc>/baixar/` | Baixa o documento (como anexo) | gestor e corretor do contrato |
| `/painel/contratos/<id>/parcelas/<parcela>/` | Parcela com as baixas; `POST` registra baixa (data, valor pago, forma e comprovante; só contrato assinado) | ver: gestor e corretor do contrato; baixa: gestor |
| `/painel/contratos/<id>/parcelas/<parcela>/baixas/<baixa>/comprovante/` | Baixa o comprovante (como anexo) | gestor e corretor do contrato |
| `POST /painel/contratos/<id>/assinar/` | Assina o contrato (data da assinatura): o imóvel fica vendido/alugado, com histórico, e sai da vitrine; um só contrato assinado por imóvel | gestor |
| `/painel/relatorios/` | Relatórios: `acervo/`, `contatos/?de=&ate=` (AAAA-MM-DD, dia de São Paulo) e `parcelas/?dias=30` (em atraso e a vencer nos próximos dias) | gestor |
| `/painel/imoveis/` | Lista os imóveis da própria empresa | corretor |
| `/painel/imoveis/novo/` | Cadastra a parte comum do imóvel | corretor |
| `/painel/imoveis/<id>/` | Página do imóvel — endereço, fotos e botão de publicar | corretor |
| `/painel/contatos/?imovel=&de=&ate=` | Caixa de contatos, com filtro por imóvel e período e o retorno anotado na própria linha | corretor (os dele) e gestor (os da empresa) |
| `POST /imovel/<id>/contato/` | Site: grava o contato e redireciona para WhatsApp, `tel:` ou `mailto:` (campo `canal`) | público, sem login |
| `/admin/` | Django Admin — cadastro de empresas, usuários e características | administrador |
| `POST /api/auth/login/` | Login da API — corpo `{ "email", "password" }`, devolve `{ "token" }` | app (gestor/corretor) |
| `GET /api/usuarios/` | Lista os usuários da própria empresa (`Authorization: Token <token>`) | app, autenticado |
| `GET/POST /api/proprietarios/?busca=` | Lista (com busca) e cadastra proprietários da própria empresa | app, corretor |
| `GET/POST /api/clientes/?busca=` e `GET/PUT/PATCH /api/clientes/<id>/` | Clientes da própria empresa (sem exclusão) | app, corretor e gestor |
| `GET/POST /api/contratos/?tipo=&situacao=` e `GET/PUT/PATCH /api/contratos/<id>/` | Contratos com as parcelas (geradas pelo servidor; sem exclusão) | app — leitura: gestor e corretor do contrato; escrita: gestor |
| `GET/POST /api/documentos-contrato/?contrato=` e `DELETE /api/documentos-contrato/<id>/` | Documentos do contrato (envio em multipart, campo `arquivo`); a resposta nunca traz o endereço do arquivo | app — leitura: gestor e corretor do contrato; envio e exclusão: gestor |
| `GET /api/documentos-contrato/<id>/baixar/` | Baixa o arquivo, conferindo empresa e perfil | app, gestor e corretor do contrato |
| `GET/POST /api/baixas/?parcela=&contrato=` e `GET /api/baixas/<id>/` | Baixas de parcela (envio em multipart, campo `comprovante`); sem edição nem exclusão | app — leitura: gestor e corretor do contrato; registro: gestor |
| `GET /api/parcelas/?contrato=&situacao=` | Parcelas com total pago, saldo, situação (`PAGA`, `A_VENCER`, `ATRASADA` ou `PREVISTA`) e dias em atraso, calculados pelo servidor (só leitura) | app — gestor (empresa) e corretor (contratos dele) |
| `GET /api/baixas/<id>/comprovante/` | Baixa o comprovante, conferindo empresa e perfil | app, gestor e corretor do contrato |
| `POST /api/contratos/<id>/assinar/` | Assina o contrato — corpo `{ "data_assinatura": "AAAA-MM-DD" }` | app, gestor |
| `GET /api/relatorios/acervo/`, `GET /api/relatorios/contatos/?de=&ate=` e `GET /api/relatorios/parcelas/?dias=30` | Os mesmos relatórios do painel (dinheiro em texto, ex.: `"1234.50"`) | app, gestor |
| `GET /api/imoveis/?busca=&situacao=&page=` | Acervo do corretor: imóveis da própria empresa, paginados (20 por página, `next` absoluto), busca por código ou título, filtro por situação (`DISPONIVEL`, `RESERVADO`, `VENDIDO`, `ALUGADO`; filtro inválido é ignorado) e só os 10 campos do cartão (`meu_imovel`, `na_vitrine` calculados no servidor) | app, corretor |
| `POST /api/imoveis/` | Cadastra imóvel da própria empresa | app, corretor |
| `GET/PUT /api/imoveis/<id>/endereco/` | Lê/grava o endereço do imóvel (cidade, CEP, latitude, longitude) | app, corretor |
| `POST /api/imoveis/<id>/publicar/` | Publica o imóvel — recusa se faltar cidade ou coordenada | app, corretor |
| `GET/POST /api/fotos-imovel/?imovel=<id>` | Lista e envia fotos (várias de uma vez, campo `imagens`) | app, corretor |
| `POST /api/fotos-imovel/<id>/definir_capa/` | Marca uma foto como capa (desmarca a anterior) | app, corretor |
| `GET /api/caracteristicas/` | Lista as características cadastradas pelo administrador | app, corretor |
| `GET /api/publico/corretores/<id>/` | Perfil público do corretor (nome, CRECI, foto, telefone, WhatsApp, e-mail, apresentação); corretor desativado ou de empresa inativa dá 404 | público — vitrine (site e app) |
| `GET /api/publico/empresas/<id>/` | Perfil público da empresa, com a lista de corretores ativos | público — vitrine (site e app) |
| `GET /api/publico/corretores/<id>/imoveis/` | Imóveis na vitrine do corretor, mais recentes primeiro, paginados como a busca da cidade; só `page` é lido; corretor inexistente, desativado ou de empresa inativa -> 404 | público — vitrine (site e app) |
| `GET /api/publico/empresas/<id>/imoveis/` | Imóveis na vitrine da empresa, mais recentes primeiro, paginados como a busca da cidade; só `page` é lido; empresa inexistente ou inativa -> 404 | público — vitrine (site e app) |
| `GET /api/publico/cidades/` | Cidades atendidas (com UF) — só as que têm imóvel publicado | público — vitrine (site e app) |
| `GET /api/publico/cidades/<id>/imoveis/` | Busca na cidade, com filtros, ordenação e paginação (ver abaixo) | público — vitrine (site e app) |
| `GET /api/publico/imoveis/<id>/` | Detalhe do imóvel: fotos, dados da natureza, endereço com coordenada e corretor | público — vitrine (site e app) |
| `POST /api/publico/imoveis/<id>/contato/` | Grava o contato — corpo `{ "canal": "WHATSAPP" \| "TELEFONE" \| "EMAIL" }`, devolve `{ "canal", "link" }` para o app abrir | público — vitrine (site e app) |
| `GET /api/contatos/?imovel=&de=&ate=` | Caixa de contatos (mesmos filtros e mesma regra de quem vê do painel) | app, corretor e gestor |
| `PATCH /api/contatos/<id>/` | Anota o retorno — corpo `{ "retorno": "FALEI" \| "VISITA_AGENDADA" \| "SEM_RESPOSTA" \| "DESCARTADO" }` | app, corretor e gestor |

## Site público (W10)

- Fica no app `site_publico` e não tem regra própria: o que é público, os filtros e a ordenação vêm de `imoveis/vitrine.py` (`buscar_na_vitrine`, `cidades_atendidas`), as mesmas funções usadas pela API.
- Os templates recebem os dados já passados pelos serializers públicos da W09 — por isso o site mostra o mesmo que o app e não tem como exibir proprietário, código interno ou documento.
- A cidade escolhida fica guardada na sessão (cookie, sem login) e aparece no topo de todas as páginas.
- Mapa com Leaflet + OpenStreetMap (gratuito, sem chave de API).
- WhatsApp: o botão chama o corretor responsável; se ele estiver desativado, chama a empresa dona do imóvel. Desde a W11 o botão passa pelo servidor, que grava o contato antes de redirecionar (ver abaixo).
- Perfis: a vitrine da empresa e a do corretor mostram os imóveis publicados de todas as cidades. Empresa inativa e corretor desativado (ou de empresa inativa) dão 404.

## Vitrine pública (API) — regras de negócio

- Não exige login nem token (e um token inválido enviado pelo app não atrapalha). Só leitura.
- Mostra imóveis de todas as empresas, mas **só publicados, com situação disponível, de empresa ativa** (regra única `Imovel.objects.na_vitrine()`, W08) — rascunho, empresa inativa e também reservado/vendido/alugado caem em 404.
- Não devolve proprietário, documento, código interno, empresa, status de publicação, complemento do endereço nem dados internos do prédio. Os serializers públicos listam campo por campo; um teste confere que nada disso aparece.
- Apartamento usa a cidade, o bairro e a coordenada do prédio.
- Parâmetros da busca (`/cidades/<id>/imoveis/`):
  - `busca` — texto livre no título, bairro e descrição; até 200 caracteres
  - `finalidade` (`VENDA`, `ALUGUEL`, `VENDA_E_ALUGUEL`) — "venda" também traz quem é "venda e aluguel", e o mesmo vale para "aluguel"
  - `natureza` (`CASA`, `APARTAMENTO`, `TERRENO`, `LOTE`) — aceita vários separados por vírgula (`natureza=CASA,LOTE`, OU entre eles); valor fora da lista dá 400
  - `preco_min` / `preco_max` — compara o preço da finalidade buscada; sem finalidade, o de venda (ou o aluguel, se não houver venda). Número só em dígitos ASCII (`123` ou `123.45`), sem expoente, `NaN`, `Infinity`, sinal de mais, espaço ou sublinhado, de 0 a 9999999999.99
  - `quartos`, `suites`, `vagas` — **mínimo** (pedir 2 quartos traz 3 também); inteiro só em dígitos ASCII, de 0 a 32767
  - `bairro` — exato, sem diferenciar maiúsculas; aceita vários separados por vírgula (`bairro=Moema,Pinheiros`, OU entre eles), até 20 itens de até 100 caracteres cada. **Risco aceito:** um bairro com vírgula no nome não consegue ser expresso nesse formato
  - `area_min` / `area_max` — área principal: construída (casa), privativa (apartamento), total (terreno), área do lote (lote). Mesmo formato numérico do preço, de 0 a 99999999.99 (o teto da coluna de área, menor que o do preço)
  - `caracteristicas=1,3` — o imóvel precisa ter **todas** as características escolhidas (ids inteiros positivos, até 20 distintos; o filtro é uma única subconsulta)
  - `ordenar` — `recentes` (padrão), `preco`, `-preco`, `area`, `-area`
  - `page` — 20 imóveis por página
- Entrada fora desses formatos e tetos responde **400** `{campo: mensagem}` em português e nunca 500 (inclusive o byte NUL `%00`, que no PostgreSQL derrubaria a consulta).
- **Limite de taxa:** as cinco rotas públicas que o app usa (lista, detalhe, cidades, bairros e características) dividem um balde por IP; passou de `VITRINE_TAXA_ANONIMA` (padrão 120/min) responde **429** com o cabeçalho `Retry-After` e corpo `{detail}`. A API autenticada do painel não é limitada. As rotas públicas `corretores/<id>/` e `empresas/<id>/` seguem sem limite. **Deploy:** configure `NUM_PROXIES` atrás de proxy reverso (o `check --deploy` avisa se faltar) e use um cache compartilhado (Redis, Memcached) com vários workers, porque o padrão é `LocMemCache` por processo e o limite efetivo multiplicaria. Muitos aparelhos atrás do mesmo IP (CGNAT) podem bater juntos no limite; ajuste a variável se for o caso.
- Opções de filtro, públicas e só de leitura:
  - `GET /api/publico/cidades/<id>/bairros/` — lista de textos, em ordem alfabética e sem repetição, com os bairros dos imóveis que estão na vitrine daquela cidade (o do prédio, para apartamento); cidade inexistente dá 404
  - `GET /api/publico/caracteristicas/` — catálogo global `[{id, nome}]` ordenado por nome, sem paginação
- A galeria do detalhe (`fotos`) começa pela capa; o app não reordena.
- Corretor desativado não aparece no detalhe (`corretor_responsavel` vem `null`), mas o imóvel continua na vitrine.

### Seed de desenvolvimento

Para ter um acervo real na vitrine durante o desenvolvimento:

```bash
cd Web
python manage.py migrate
python manage.py semear_vitrine_dev
```

- Só roda com `DEBUG=True` (com `DEBUG=False` recusa com erro e não cria nada).
- É idempotente: rodar de novo não duplica nada, e uma execução interrompida é retomada rodando outra vez.
- Mexe só na empresa de demonstração "Imobiliária Vitrine Demo (dev)"; nenhuma outra empresa é lida ou alterada.
- Cria imóveis das 4 naturezas (com loteamento, quadra e lote) em Campinas (24), Valinhos, Vinhedo e Indaiatuba (4 cada), com fotos geradas na hora. Códigos no formato `DEV-<CIDADE>-<NN>`.
- Os usuários criados (corretores) ficam com senha inutilizável — não há credencial padrão.
- O emulador Android enxerga o servidor de desenvolvimento como `10.0.2.2`; mantenha esse host no `ALLOWED_HOSTS` (como no `Web/.env.example`).

## Contatos (W11) — regras de negócio

- **O servidor monta o link.** Os botões da página do imóvel (WhatsApp, Ligar, Enviar e-mail) não levam o link pronto: enviam um POST para `/imovel/<id>/contato/`, que grava o contato e redireciona. O app faz o mesmo pela API e recebe o link para abrir. As regras ficam em `contatos/servicos.py`.
- **POST, não GET.** Robôs, pré-carregamento do navegador e prévias de link fazem GET sozinhos; só um clique envia o formulário. GET no endereço de contato dá 405 e não grava nada.
- **Mensagem padrão** com o título, o **código interno** do imóvel e o link do anúncio. O código continua fora da vitrine (HTML e JSON, regra da W09): ele só sai do servidor dentro do link, no momento do clique — o visitante o vê no texto da mensagem antes de enviar.
- **Para quem vai:** corretor responsável; se ele estiver desativado, a empresa dona do imóvel (o contato fica com `corretor` vazio e só o gestor vê). Autônomo sem telefone/WhatsApp próprio usa os da empresa. Canal sem número/e-mail não aparece na página e é recusado na API.
- **Só imóvel na vitrine recebe contato** (publicado, de empresa ativa) — usa `imoveis_da_vitrine()`, então segue qualquer regra nova da vitrine.
- **Guarda o contato, não a conversa:** imóvel, corretor, canal, data e o retorno. Nenhum dado do visitante (nem campo de texto livre).
- **Quem vê:** sempre filtrado pela empresa do usuário; o corretor vê só os contatos que recebeu, o gestor vê todos da empresa, o administrador não vê nenhum. Contato de fora cai em 404. Corretor e gestor podem anotar o retorno (falei, visita agendada, sem resposta, descartado).
- Os botões dos perfis (empresa e corretor) continuam indo direto para o WhatsApp, sem registro: o contato pertence a um imóvel, e o perfil não tem um.
- Limite conhecido: registramos o clique. Quem copiar o número na mão não vira contato. Ainda não há limite de requisições por visitante no endereço público.

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
