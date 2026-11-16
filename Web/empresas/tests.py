import io
import shutil
import tempfile
from unittest import mock

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from contas.models import Usuario
from core.throttles import ThrottleVitrinePublica
from imoveis.models import (
    Caracteristica,
    FotoImovel,
    Imovel,
    Lote,
    Loteamento,
    Predio,
    Quadra,
)
from imoveis.vitrine import imoveis_da_vitrine
from localizacao.models import Cidade
from proprietarios.models import Proprietario

from .management.commands.semear_vitrine_dev import NOME_EMPRESA_DEMO
from .models import Empresa


class EmpresaModelTests(TestCase):
    def test_imobiliaria_precisa_de_cnpj_e_nao_pode_ter_cpf(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária X",
            cpf="111.111.111-11",
            creci="12345-J",
            whatsapp="11999999999",
            email="contato@x.com",
        )
        with self.assertRaises(ValidationError):
            empresa.full_clean()

    def test_autonomo_precisa_de_cpf_e_nao_pode_ter_cnpj(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.AUTONOMO,
            razao_social_ou_nome="Corretor Y",
            cnpj="11.111.111/0001-11",
            creci="54321-F",
            whatsapp="11999999999",
            email="contato@y.com",
        )
        with self.assertRaises(ValidationError):
            empresa.full_clean()

    def test_imobiliaria_com_cnpj_e_valida(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária X",
            cnpj="74.286.058/0001-80",
            creci="12345-J",
            whatsapp="11999999999",
            email="contato@x.com",
        )
        empresa.full_clean()  # não deve levantar erro

    def test_cnpj_com_digito_verificador_invalido_e_recusado(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária X",
            cnpj="11.111.111/0001-11",
            creci="12345-J",
            whatsapp="11999999999",
            email="contato@x.com",
        )
        with self.assertRaises(ValidationError):
            empresa.full_clean()

    def test_cnpj_e_normalizado_para_somente_digitos(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária X",
            cnpj="74.286.058/0001-80",
            creci="12345-J",
            whatsapp="11999999999",
            email="contato@x.com",
        )
        empresa.full_clean()
        self.assertEqual(empresa.cnpj, "74286058000180")


MEDIA_DO_SEED = tempfile.mkdtemp()


@override_settings(DEBUG=True, MEDIA_ROOT=MEDIA_DO_SEED)
class SemearVitrineDevTests(TestCase):
    """O seed de desenvolvimento (D-09): o acervo semeado sai pela vitrine pública REAL."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_DO_SEED, ignore_errors=True)
        super().tearDownClass()

    @classmethod
    def setUpTestData(cls):
        call_command("semear_vitrine_dev", stdout=io.StringIO())
        cls.campinas = Cidade.objects.get(nome="Campinas", uf="SP")

    def setUp(self):
        # o limite de taxa guarda o histórico no cache; cada teste começa do zero e com taxa alta fixa,
        # sem depender do VITRINE_TAXA_ANONIMA do `.env` de quem roda (WR-09)
        cache.clear()
        patcher = mock.patch.object(ThrottleVitrinePublica, "rate", "100000/min", create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()  # sem credenciais: a vitrine é pública

    def test_vitrine_de_campinas_entrega_as_quatro_naturezas_com_o_bloco_preenchido(self):
        cidades = self.client.get(reverse("publico-cidades"))
        self.assertEqual(cidades.status_code, status.HTTP_200_OK)
        self.assertIn("Campinas", [c["nome"] for c in cidades.data])

        lista = self.client.get(reverse("publico-vitrine-cidade", args=[self.campinas.pk]))
        self.assertEqual(lista.status_code, status.HTTP_200_OK, lista.data)
        itens = lista.data["results"]
        self.assertEqual({i["natureza"] for i in itens}, set(Imovel.Natureza.values))

        bloco_por_natureza = {
            "CASA": "casa", "APARTAMENTO": "apartamento", "TERRENO": "terreno", "LOTE": "lote",
        }
        for natureza, bloco in bloco_por_natureza.items():
            primeiro = next(i for i in itens if i["natureza"] == natureza)
            detalhe = self.client.get(reverse("publico-imovel", args=[primeiro["id"]]))
            self.assertEqual(detalhe.status_code, status.HTTP_200_OK, detalhe.data)
            self.assertEqual(detalhe.data["natureza"], natureza)
            self.assertIsNotNone(detalhe.data[bloco], f"bloco {bloco} vazio")
            if natureza == "LOTE":
                self.assertIsInstance(detalhe.data["lote"]["quadra"]["loteamento"], dict)

    # ------------------------------------------------------------------ Task 2

    def _recentes_de_campinas(self):
        return list(
            imoveis_da_vitrine()
            .filter(cidade_anuncio=self.campinas.pk)
            .order_by("-criado_em", "-id")[:20]
        )

    def test_cada_cidade_tem_imoveis_e_campinas_passa_de_uma_pagina(self):
        for nome, _uf in [("Campinas", "SP"), ("Valinhos", "SP"), ("Vinhedo", "SP"), ("Indaiatuba", "SP")]:
            cidade = Cidade.objects.get(nome=nome)
            da_cidade = imoveis_da_vitrine().filter(cidade_anuncio=cidade.pk)
            self.assertGreaterEqual(da_cidade.count(), 4, nome)
            self.assertEqual(
                set(da_cidade.values_list("natureza", flat=True)), set(Imovel.Natureza.values), nome
            )
            if nome != "Campinas":
                self.assertEqual(da_cidade.count(), 4, nome)
        self.assertGreater(imoveis_da_vitrine().filter(cidade_anuncio=self.campinas.pk).count(), 20)

    def test_as_quatro_naturezas_estao_entre_os_20_mais_recentes_e_a_segunda_pagina_existe(self):
        recentes = self._recentes_de_campinas()
        self.assertEqual({i.natureza for i in recentes}, set(Imovel.Natureza.values))
        url = reverse("publico-vitrine-cidade", args=[self.campinas.pk])
        pagina_1 = self.client.get(url, {"ordenar": "recentes"})
        self.assertEqual(len(pagina_1.data["results"]), 20)
        self.assertEqual([i["id"] for i in pagina_1.data["results"]], [i.pk for i in recentes])
        self.assertIsNotNone(pagina_1.data["next"])

    def test_casos_de_borda_entre_os_20_mais_recentes_de_campinas(self):
        recentes = self._recentes_de_campinas()
        sem_foto = [i for i in recentes if not i.fotos.exists()]
        self.assertEqual(len(sem_foto), 1)
        inativos = [i for i in recentes if not i.corretor_responsavel.is_active]
        self.assertEqual(len(inativos), 1)
        capa_fora_da_primeira = [
            i for i in recentes if i.fotos.filter(capa=True).exclude(ordem=0).exists()
        ]
        self.assertGreaterEqual(len(capa_fora_da_primeira), 1)
        self.assertTrue(capa_fora_da_primeira[0].fotos.filter(capa=True, ordem=2).exists())
        # a vitrine devolve o que o app consome: sem foto -> foto_capa nulo; inativo -> sem contato
        detalhe = self.client.get(reverse("publico-imovel", args=[inativos[0].pk]))
        self.assertIsNone(detalhe.data["corretor_responsavel"])
        lista = self.client.get(reverse("publico-vitrine-cidade", args=[self.campinas.pk]))
        capas = {i["id"]: i["foto_capa"] for i in lista.data["results"]}
        self.assertIsNone(capas[sem_foto[0].pk])
        self.assertEqual(sum(1 for v in capas.values() if v is None), 1)

    def test_tres_finalidades_aparecem_em_campinas(self):
        finalidades = set(
            imoveis_da_vitrine().filter(cidade_anuncio=self.campinas.pk)
            .values_list("finalidade", flat=True)
        )
        self.assertEqual(finalidades, set(Imovel.Finalidade.values))

    def test_lote_semeado_tem_a_cidade_do_loteamento_igual_a_do_endereco(self):
        lotes = Lote.objects.select_related("imovel__endereco", "quadra__loteamento")
        self.assertGreaterEqual(lotes.count(), 9)
        for lote in lotes:
            self.assertEqual(
                lote.quadra.loteamento.cidade_id, lote.imovel.endereco.cidade_id, lote.imovel.codigo
            )

    def test_campinas_tem_dois_predios_e_um_loteamento_com_duas_quadras(self):
        self.assertEqual(Predio.objects.filter(endereco__cidade=self.campinas).count(), 2)
        loteamentos = Loteamento.objects.filter(cidade=self.campinas)
        self.assertEqual(loteamentos.count(), 1)
        self.assertEqual(loteamentos.get().quadras.count(), 2)

    def test_codigos_seguem_o_padrao_dev_cidade_numero(self):
        codigos = set(Imovel.objects.values_list("codigo", flat=True))
        self.assertIn("DEV-CAMPINAS-01", codigos)
        self.assertIn("DEV-CAMPINAS-24", codigos)
        self.assertIn("DEV-INDAIATUBA-04", codigos)

    def test_usuarios_do_seed_tem_senha_inutilizavel(self):
        usuarios = Usuario.objects.all()
        self.assertGreaterEqual(usuarios.count(), 2)
        for usuario in usuarios:
            self.assertFalse(usuario.has_usable_password(), usuario.email)

    def _contagens(self):
        return {
            "imoveis": Imovel.objects.count(), "fotos": FotoImovel.objects.count(),
            "lotes": Lote.objects.count(), "quadras": Quadra.objects.count(),
            "loteamentos": Loteamento.objects.count(), "predios": Predio.objects.count(),
            "caracteristicas": Caracteristica.objects.count(),
            "corretores": Usuario.objects.filter(empresa__razao_social_ou_nome=NOME_EMPRESA_DEMO).count(),
            "empresas": Empresa.objects.count(), "proprietarios": Proprietario.objects.count(),
            "cidades": Cidade.objects.count(),
        }

    def test_rodar_duas_vezes_nao_duplica_e_nao_mexe_em_outra_empresa(self):
        outra = Empresa.objects.create(
            tipo=Empresa.Tipo.IMOBILIARIA, razao_social_ou_nome="Outra Imobiliária",
            cnpj="74.286.058/0001-80", creci="99999-J", whatsapp="11999999999",
            email="outra@example.com",
        )
        corretor = Usuario.objects.create_user(
            email="outra.corretor@example.com", senha="senha-forte-123", nome="Outro",
            perfil=Usuario.Perfil.CORRETOR, empresa=outra, creci="11111-F",
            telefone="11911111111", whatsapp="11911111111",
        )
        dono = Proprietario.objects.create(
            empresa=outra, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Dono Outra", documento="52998224725",
        )
        imovel = Imovel.objects.create(
            empresa=outra, codigo="DEV-CAMPINAS-01", titulo="Imóvel da outra",
            natureza=Imovel.Natureza.CASA, corretor_responsavel=corretor, proprietario=dono,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000.00",
        )
        antes = self._contagens()
        call_command("semear_vitrine_dev", stdout=io.StringIO())
        call_command("semear_vitrine_dev", stdout=io.StringIO())
        self.assertEqual(self._contagens(), antes)

        imovel.refresh_from_db()
        self.assertEqual(imovel.titulo, "Imóvel da outra")
        self.assertFalse(imovel.publicado)
        outra.refresh_from_db()
        self.assertEqual(outra.razao_social_ou_nome, "Outra Imobiliária")
        self.assertFalse(outra.cidades_atuacao.exists())

    def test_execucao_interrompida_e_retomada_sem_duplicar(self):
        # simula a interrupção: o último imóvel some (a transação dele não chegou a fechar)
        ultimo = Imovel.objects.get(codigo="DEV-CAMPINAS-24")
        antes = Imovel.objects.count()
        ultimo.delete()
        self.assertEqual(Imovel.objects.count(), antes - 1)
        call_command("semear_vitrine_dev", stdout=io.StringIO())
        self.assertEqual(Imovel.objects.count(), antes)
        recriado = Imovel.objects.get(codigo="DEV-CAMPINAS-24")
        self.assertTrue(recriado.publicado)
        self.assertTrue(recriado.fotos.exists())
        # o imóvel propositalmente sem foto continua sem foto depois da retomada
        self.assertEqual(sum(1 for i in self._recentes_de_campinas() if not i.fotos.exists()), 1)


@override_settings(DEBUG=False, MEDIA_ROOT=MEDIA_DO_SEED)
class SemearVitrineDevDebugDesligadoTests(TestCase):
    def test_com_debug_desligado_recusa_e_nao_cria_nada(self):
        with self.assertRaises(CommandError):
            call_command("semear_vitrine_dev", stdout=io.StringIO())
        self.assertEqual(Empresa.objects.count(), 0)
        self.assertEqual(Cidade.objects.count(), 0)
        self.assertEqual(Imovel.objects.count(), 0)
        self.assertEqual(Usuario.objects.count(), 0)
