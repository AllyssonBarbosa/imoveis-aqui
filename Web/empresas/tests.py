from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from localizacao.models import Cidade

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


class CidadesPublicasAPITests(APITestCase):
    """GET /api/publico/cidades/ — sem token, só cidades atendidas (API-02)."""

    def _criar_empresa_ativa(self, nome, cnpj, cidades, ativa=True):
        empresa = Empresa.objects.create(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome=nome,
            cnpj=cnpj,
            creci="12345-J",
            whatsapp="11999999999",
            email=f"{cnpj}@example.com",
            ativa=ativa,
        )
        empresa.cidades_atuacao.set(cidades)
        return empresa

    def test_lista_cidades_publica_sem_autenticacao_200(self):
        campinas = Cidade.objects.create(nome="Campinas", uf="SP")
        self._criar_empresa_ativa("Imobiliária Campinas", "11222333000009", [campinas])

        resposta = self.client.get(reverse("publico-cidades"))

        self.assertEqual(resposta.status_code, status.HTTP_200_OK)
        self.assertEqual(set(resposta.data.keys()), {"next", "previous", "results"})

    def test_somente_cidades_atendidas_aparecem_uma_unica_vez(self):
        campinas = Cidade.objects.create(nome="Campinas", uf="SP")
        valinhos = Cidade.objects.create(nome="Valinhos", uf="SP")
        Cidade.objects.create(nome="Sorocaba", uf="SP")  # sem nenhuma empresa
        jundiai = Cidade.objects.create(nome="Jundiaí", uf="SP")

        self._criar_empresa_ativa("Imobiliária A", "11222333000009", [campinas])
        self._criar_empresa_ativa("Imobiliária B", "22333444000181", [campinas])
        self._criar_empresa_ativa("Imobiliária C", "33444555000262", [valinhos])
        self._criar_empresa_ativa(
            "Imobiliária Inativa", "44555666000343", [jundiai], ativa=False
        )

        resposta = self.client.get(reverse("publico-cidades"))

        nomes = [linha["nome"] for linha in resposta.data["results"]]
        self.assertEqual(nomes, ["Campinas", "Valinhos"])
        self.assertEqual(len(resposta.data["results"]), 2)

    def test_cada_linha_tem_somente_id_nome_uf(self):
        campinas = Cidade.objects.create(nome="Campinas", uf="SP")
        self._criar_empresa_ativa("Imobiliária Campinas", "11222333000009", [campinas])

        resposta = self.client.get(reverse("publico-cidades"))

        self.assertEqual(set(resposta.data["results"][0].keys()), {"id", "nome", "uf"})

    def test_post_retorna_405(self):
        resposta = self.client.post(reverse("publico-cidades"), {})

        self.assertEqual(resposta.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_paginacao_cursor_50_por_pagina_sem_duplicar_com_insercao_concorrente(self):
        empresa = Empresa.objects.create(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária Vitrine 55",
            cnpj="55666777000424",
            creci="12345-J",
            whatsapp="11999999999",
            email="vitrine55@example.com",
        )
        cidades = [
            Cidade.objects.create(nome=f"Cidade{indice:03d}", uf="SP")
            for indice in range(1, 56)
        ]
        empresa.cidades_atuacao.set(cidades)
        ids_originais = {cidade.id for cidade in cidades}

        primeira_pagina = self.client.get(reverse("publico-cidades"))
        self.assertEqual(len(primeira_pagina.data["results"]), 50)
        self.assertIsNotNone(primeira_pagina.data["next"])
        self.assertIn("cursor=", primeira_pagina.data["next"])

        # Cidade cadastrada entre as duas requisições, posicionada ANTES do
        # cursor da página 1 (entre Cidade025 e Cidade026) — não deve afetar
        # nem duplicar nenhum item da página 2.
        nova_cidade = Cidade.objects.create(nome="Cidade025-Nova", uf="SP")
        empresa.cidades_atuacao.add(nova_cidade)

        segunda_pagina = self.client.get(primeira_pagina.data["next"])
        self.assertEqual(len(segunda_pagina.data["results"]), 5)
        self.assertIsNone(segunda_pagina.data["next"])

        ids_pagina1 = {linha["id"] for linha in primeira_pagina.data["results"]}
        ids_pagina2 = {linha["id"] for linha in segunda_pagina.data["results"]}
        self.assertEqual(len(ids_pagina1 & ids_pagina2), 0)
        self.assertEqual(ids_pagina1 | ids_pagina2, ids_originais)


class SemearVitrineDevTests(TestCase):
    """Comando de seed de desenvolvimento — idempotente e só roda com DEBUG=True."""

    CIDADES_ESPERADAS = {
        ("Campinas", "SP"),
        ("Valinhos", "SP"),
        ("Vinhedo", "SP"),
        ("Indaiatuba", "SP"),
    }

    @override_settings(DEBUG=True)
    def test_comando_e_idempotente_e_cria_as_4_cidades_com_empresa_demo_ativa(self):
        call_command("semear_vitrine_dev")
        call_command("semear_vitrine_dev")  # rodar duas vezes não deve duplicar nada

        nomes_esperados = [nome for nome, _ in self.CIDADES_ESPERADAS]
        cidades_criadas = set(
            Cidade.objects.filter(nome__in=nomes_esperados).values_list("nome", "uf")
        )
        self.assertEqual(cidades_criadas, self.CIDADES_ESPERADAS)

        empresas_demo = Empresa.objects.filter(
            ativa=True, cidades_atuacao__nome__in=nomes_esperados
        ).distinct()
        self.assertEqual(empresas_demo.count(), 1)
        empresa_demo = empresas_demo.first()
        self.assertEqual(
            set(empresa_demo.cidades_atuacao.values_list("nome", "uf")),
            self.CIDADES_ESPERADAS,
        )

        resposta = self.client.get(reverse("publico-cidades"))
        nomes_listados = {linha["nome"] for linha in resposta.data["results"]}
        self.assertEqual(nomes_listados, {"Campinas", "Indaiatuba", "Valinhos", "Vinhedo"})

    @override_settings(DEBUG=False)
    def test_comando_recusa_rodar_fora_de_debug(self):
        with self.assertRaises(CommandError):
            call_command("semear_vitrine_dev")

        self.assertEqual(Cidade.objects.count(), 0)
