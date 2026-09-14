# Instruções do projeto — Imóveis Aqui

## Comportamento do Claude Code

- Nunca faça commits (`git commit`) ou push sem autorização explícita minha. Prepare as alterações, mas peça confirmação antes.
- Se não entender algo que eu pedi, pergunte antes de agir — não assuma o que eu quis dizer.
- Sempre explique em português o que vai ser feito antes de mexer em vários arquivos de uma vez.

## Stack do projeto

- Backend + Web (site público e painel): Django + Django REST Framework (Python)
- Banco de dados: PostgreSQL
- App mobile: Flutter (Dart), consumindo a API do Django
- Estrutura de pastas: `Web/` (Django) e `App/` (Flutter), na raiz do repositório

## Regras de negócio obrigatórias

- Nenhuma regra de negócio (cálculo de parcela, situação de atraso, mudança de status do imóvel) pode ser decidida no Flutter — só no Django. O app é uma tela sobre os dados, não a dona da regra.
- Todo dado do acervo (imóvel, contrato, etc.) pertence a uma empresa (imobiliária ou corretor autônomo) — nunca escreva uma consulta que não filtre pela empresa do usuário logado (isolamento multitenant).
- O administrador não pertence a nenhuma empresa; gestor e corretor sempre pertencem a uma.
- A vitrine pública (site e app) não exige login — login é só pra quem trabalha (corretor, gestor, administrador).

## Segurança

- Nunca sugira colocar senha, token ou dado sensível direto no código — sempre usar variáveis de ambiente (`.env`), que já está no `.gitignore`.
- Não crie ou edite o `.gitignore` sem me avisar antes.

## Idioma

- Nomeie models, variáveis e comentários em português, seguindo o padrão que já vem sendo usado no projeto.