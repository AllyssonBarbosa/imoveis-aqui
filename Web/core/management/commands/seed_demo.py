from django.core.management.base import BaseCommand

from contas.models import Usuario
from empresas.models import Empresa
from imoveis.models import Caracteristica
from localizacao.models import Cidade
from proprietarios.models import Proprietario


class Command(BaseCommand):
    help = "Cria os dados de teste (empresa, usuários, cidade, características, proprietário)."

    def handle(self, *args, **options):
        cidade, _ = Cidade.objects.get_or_create(nome="São Paulo", uf="SP")

        empresa, criada = Empresa.objects.get_or_create(
            razao_social_ou_nome="Imobiliaria Demo",
            defaults=dict(
                tipo=Empresa.Tipo.IMOBILIARIA,
                cnpj="74286058000180",
                creci="11111-J",
                whatsapp="11988887777",
                email="demo@imoveisaqui.com",
            ),
        )
        if criada:
            empresa.cidades_atuacao.add(cidade)

        if not Usuario.objects.filter(email="admin@imoveisaqui.com").exists():
            Usuario.objects.create_superuser(
                email="admin@imoveisaqui.com", senha="TrocarSenha123", nome="Administrador"
            )

        if not Usuario.objects.filter(email="gestor@demo.com").exists():
            Usuario.objects.create_user(
                email="gestor@demo.com", senha="SenhaForte123", nome="Gestor Demo",
                perfil=Usuario.Perfil.GESTOR, empresa=empresa,
            )

        if not Usuario.objects.filter(email="corretor@demo.com").exists():
            Usuario.objects.create_user(
                email="corretor@demo.com", senha="SenhaForte123", nome="Corretor Demo",
                perfil=Usuario.Perfil.CORRETOR, empresa=empresa,
                creci="11111-F", telefone="11988887777", whatsapp="11988887777",
                apresentacao="Corretor de demonstração.",
            )

        for nome in ["Portão eletrônico", "Ar-condicionado", "Cozinha planejada", "Closet"]:
            Caracteristica.objects.get_or_create(nome=nome)

        Proprietario.objects.get_or_create(
            empresa=empresa, documento="58763329689",
            defaults=dict(
                tipo_pessoa=Proprietario.TipoPessoa.FISICA,
                nome_razao_social="Maria Proprietária",
                telefone="11966665555", email="maria@teste.com",
            ),
        )

        self.stdout.write(self.style.SUCCESS("Dados de teste prontos."))
        self.stdout.write("  administrador: admin@imoveisaqui.com / TrocarSenha123")
        self.stdout.write("  gestor:        gestor@demo.com / SenhaForte123")
        self.stdout.write("  corretor:      corretor@demo.com / SenhaForte123")
