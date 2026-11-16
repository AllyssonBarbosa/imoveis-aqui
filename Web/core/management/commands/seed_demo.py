from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from clientes.models import Cliente
from contas.models import Usuario
from contratos.calculos import somar_meses
from contratos.models import Contrato
from contratos.servicos import assinar_contrato, salvar_contrato
from empresas.models import Empresa
from imoveis.models import Caracteristica, Imovel
from localizacao.models import Cidade
from proprietarios.models import Proprietario


class Command(BaseCommand):
    help = (
        "Cria os dados de teste (empresa, usuários, cidade, características, proprietário, "
        "imóvel, cliente e um contrato de venda assinado, com parcelas)."
    )

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

        proprietario, _ = Proprietario.objects.get_or_create(
            empresa=empresa, documento="58763329689",
            defaults=dict(
                tipo_pessoa=Proprietario.TipoPessoa.FISICA,
                nome_razao_social="Maria Proprietária",
                telefone="11966665555", email="maria@teste.com",
            ),
        )

        self._contrato_de_exemplo(empresa, proprietario)

        self.stdout.write(self.style.SUCCESS("Dados de teste prontos."))
        self.stdout.write("  administrador: admin@imoveisaqui.com / TrocarSenha123")
        self.stdout.write("  gestor:        gestor@demo.com / SenhaForte123")
        self.stdout.write("  corretor:      corretor@demo.com / SenhaForte123")

    def _contrato_de_exemplo(self, empresa, proprietario):
        """Um imóvel, um cliente e um contrato de venda (W12), sem duplicar se rodar de novo.

        O contrato começa 4 meses antes de hoje, para já ter parcelas vencidas
        e a vencer quando a baixa e o atraso (etapas 5 e 6) estiverem prontos.
        """
        corretor = Usuario.objects.get(email="corretor@demo.com")
        gestor = Usuario.objects.get(email="gestor@demo.com")

        imovel, _ = Imovel.objects.get_or_create(
            empresa=empresa, codigo="DEMO-CASA-01",
            defaults=dict(
                titulo="Casa de demonstração com 3 quartos", natureza=Imovel.Natureza.CASA,
                corretor_responsavel=corretor, proprietario=proprietario,
                finalidade=Imovel.Finalidade.VENDA, preco_venda=Decimal("350000.00"),
            ),
        )

        cliente, _ = Cliente.objects.get_or_create(
            empresa=empresa, documento="52998224725",
            defaults=dict(
                tipo_pessoa=Cliente.TipoPessoa.FISICA, nome_razao_social="João Comprador",
                telefone="11955554444", email="joao.comprador@teste.com",
            ),
        )

        if not Contrato.objects.filter(empresa=empresa, imovel=imovel).exists():
            hoje = timezone.localdate()
            contrato = salvar_contrato(
                Contrato(
                    empresa=empresa, tipo=Contrato.Tipo.VENDA, imovel=imovel, cliente=cliente,
                    corretor=corretor, valor=Decimal("350000.00"), entrada=Decimal("50000.00"),
                    numero_parcelas=60, dia_vencimento=10,
                    data_inicio=somar_meses(hoje, -4, 1),
                    observacoes="Contrato de demonstração criado pelo seed_demo.",
                ),
                gestor,
            )
            # assinado no dia do início: o imóvel fica "vendido" e fora da vitrine
            assinar_contrato(contrato, gestor, contrato.data_inicio)
