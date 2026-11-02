from unittest import mock

from django.urls import reverse
from rest_framework import status

from imoveis.models import Imovel
from imoveis.tests import (
    VitrineBase, criar_corretor, criar_empresa, criar_endereco, criar_gestor, criar_imovel,
    criar_proprietario,
)
from imoveis.vitrine import ORDENACOES
from localizacao.models import Cidade

from .views import CHAVE_CIDADE, ROTULOS_ORDENACAO, link_whatsapp


class SiteBase(VitrineBase):
    """Mesmo cenário da vitrine da API (W09): o site tem que mostrar o mesmo que ela."""

    def pagina(self, nome, *args, **params):
        return self.client.get(reverse(f"site_publico:{nome}", args=args), params)

    def ids_site(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [cartao["id"] for cartao in response.context["imoveis"]]


class InicioECidadeTests(SiteBase):
    """Critério 1: início com a escolha da cidade, que fica valendo na navegação."""

    def test_inicio_lista_so_as_cidades_atendidas(self):
        Cidade.objects.create(nome="Recife", uf="PE")  # sem imóvel nenhum
        response = self.pagina("inicio")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertCountEqual(list(response.context["cidades"]), [self.sp, self.campinas])
        self.assertContains(response, reverse("site_publico:vitrine", args=[self.sp.pk]))
        self.assertNotContains(response, "Recife")

    def test_cidade_escolhida_fica_valendo_nas_outras_paginas(self):
        self.pagina("vitrine", self.sp.pk)
        self.assertEqual(self.client.session[CHAVE_CIDADE], self.sp.pk)

        link_da_cidade = reverse("site_publico:vitrine", args=[self.sp.pk])
        for response in (
            self.pagina("imovel", self.casa.pk),
            self.pagina("empresa", self.empresa.pk),
            self.pagina("corretor", self.corretor.pk),
        ):
            self.assertContains(response, "Imóveis em São Paulo/SP")
            self.assertContains(response, link_da_cidade)
        self.assertContains(self.pagina("inicio"), "Continuar em São Paulo/SP")

    def test_trocar_de_cidade_substitui_a_escolhida(self):
        self.pagina("vitrine", self.sp.pk)
        self.pagina("vitrine", self.campinas.pk)
        self.assertEqual(self.client.session[CHAVE_CIDADE], self.campinas.pk)
        self.assertContains(self.pagina("inicio"), "Continuar em Campinas/SP")

    def test_cidade_inexistente_da_404(self):
        self.assertEqual(self.pagina("vitrine", 99999).status_code, status.HTTP_404_NOT_FOUND)


class VitrineSiteTests(SiteBase):
    """Critério 2: mesmos filtros e mesma ordenação da API."""

    COMBINACOES = [
        {},
        {"busca": "quintal"},
        {"busca": "bela vista"},
        {"finalidade": "VENDA"},
        {"finalidade": "ALUGUEL"},
        {"natureza": "APARTAMENTO"},
        {"finalidade": "VENDA", "preco_min": "300000", "preco_max": "450000"},
        {"finalidade": "ALUGUEL", "preco_max": "2800"},
        {"quartos": "2"},
        {"suites": "1"},
        {"vagas": "2"},
        {"bairro": "moema"},
        {"area_min": "100"},
        {"area_max": "100"},
        {"ordenar": "recentes"},
        {"finalidade": "VENDA", "ordenar": "preco"},
        {"finalidade": "VENDA", "ordenar": "-preco"},
        {"ordenar": "area"},
        {"ordenar": "-area"},
    ]

    def test_site_e_api_devolvem_os_mesmos_imoveis_na_mesma_ordem(self):
        for params in self.COMBINACOES:
            with self.subTest(params=params):
                self.assertEqual(
                    self.ids_site(self.pagina("vitrine", self.sp.pk, **params)),
                    self.ids(self.buscar(**params)),
                )

    def test_caracteristicas_marcadas_no_formulario_valem_como_na_api(self):
        # no formulário são caixinhas (?caracteristicas=1&caracteristicas=3); na API, "1,3"
        no_site = self.ids_site(
            self.pagina("vitrine", self.sp.pk, caracteristicas=[self.portao.pk, self.ar.pk])
        )
        na_api = self.ids(self.buscar(caracteristicas=f"{self.portao.pk},{self.ar.pk}"))
        self.assertEqual(no_site, na_api)
        self.assertEqual(no_site, [self.casa.pk])

    def test_formulario_tem_todos_os_filtros_da_api(self):
        response = self.pagina("vitrine", self.sp.pk)
        for nome in (
            "busca", "finalidade", "natureza", "preco_min", "preco_max", "quartos", "suites",
            "vagas", "bairro", "area_min", "area_max", "caracteristicas", "ordenar",
        ):
            self.assertContains(response, f'name="{nome}"')
        # as opções de ordenar são exatamente as da API
        self.assertEqual(set(ROTULOS_ORDENACAO), set(ORDENACOES))

    def test_filtro_invalido_mostra_mensagem_em_vez_de_erro(self):
        response = self.pagina("vitrine", self.sp.pk, preco_min="barato")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Informe um número válido.")
        self.assertEqual(self.ids_site(response), [])

    def test_paginacao_mantem_os_filtros(self):
        with mock.patch("site_publico.views.IMOVEIS_POR_PAGINA", 3):
            pagina1 = self.pagina("vitrine", self.sp.pk, ordenar="area")
            pagina2 = self.pagina("vitrine", self.sp.pk, ordenar="area", page=2)
        self.assertEqual(len(self.ids_site(pagina1)), 3)
        self.assertEqual(self.ids_site(pagina2), [self.terreno.pk])
        self.assertContains(pagina1, "?ordenar=area&page=2")

    def test_cartao_mostra_capa_preco_e_numeros(self):
        response = self.pagina("vitrine", self.sp.pk)
        self.assertContains(response, self.casa.fotos.get(capa=True).imagem.url)
        self.assertContains(response, "R$ 500.000,00")
        self.assertContains(response, "3 quartos")
        self.assertContains(response, reverse("site_publico:imovel", args=[self.casa.pk]))


class PaginaImovelTests(SiteBase):
    """Critério 3: galeria, dados da natureza, valores e mapa."""

    def test_casa_com_galeria_valores_dados_e_mapa(self):
        response = self.pagina("imovel", self.casa.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # galeria: uma miniatura por foto
        self.assertContains(response, "data-galeria-foto=", count=2)
        for foto in self.casa.fotos.all():
            self.assertContains(response, foto.imagem.url)
        # valores
        self.assertContains(response, "R$ 500.000,00")
        # dados da natureza
        self.assertContains(response, "Dados da casa")
        self.assertContains(response, "150 m²")  # construída e área livre
        self.assertContains(response, "3 quartos (1 suíte)")
        self.assertContains(response, "Portão eletrônico")
        # mapa (Leaflet + OpenStreetMap) com a coordenada do endereço
        self.assertContains(response, 'data-latitude="-23.600000"')
        self.assertContains(response, 'data-longitude="-46.660000"')
        self.assertContains(response, "leaflet")

    def test_apartamento_mostra_dados_do_predio_e_endereco_dele(self):
        response = self.pagina("imovel", self.apto.pk)
        self.assertContains(response, "Dados do apartamento")
        self.assertContains(response, "Edifício Aurora")
        self.assertContains(response, "Bela Vista")
        self.assertContains(response, "R$ 400.000,00")
        self.assertContains(response, "R$ 2.500,00/mês")
        self.assertContains(response, 'data-latitude="-23.560000"')

    def test_terreno(self):
        response = self.pagina("imovel", self.terreno.pk)
        self.assertContains(response, "Dados do terreno")
        self.assertContains(response, "360 m²")
        self.assertContains(response, "Plano")

    def test_rascunho_e_empresa_inativa_dao_404(self):
        self.assertEqual(self.pagina("imovel", self.rascunho.pk).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.pagina("imovel", self.da_inativa.pk).status_code, status.HTTP_404_NOT_FOUND)

    def test_leva_ao_perfil_do_corretor_e_da_empresa(self):
        response = self.pagina("imovel", self.casa.pk)
        self.assertContains(response, reverse("site_publico:corretor", args=[self.corretor.pk]))
        self.assertContains(response, reverse("site_publico:empresa", args=[self.empresa.pk]))


class PerfisTests(SiteBase):
    """Critério 4: perfil da empresa e do corretor, cada um com a vitrine dos seus imóveis."""

    def setUp(self):
        super().setUp()
        # outra empresa ativa, com imóvel publicado na mesma cidade
        self.outra = criar_empresa(nome="Outra Imobiliária", cnpj="11.444.777/0001-61")
        self.corretor_da_outra = criar_corretor(self.outra, email="outra@teste.com")
        self.imovel_da_outra = criar_imovel(
            self.outra, self.corretor_da_outra, criar_proprietario(self.outra),
            codigo="OUTRA-1", titulo="Casa da outra", endereco=criar_endereco(self.sp, "Moema"),
            publicado=True,
        )
        # segundo corretor da mesma empresa, com um imóvel só dele
        self.colega = criar_corretor(self.empresa, email="colega@teste.com")
        self.imovel_do_colega = criar_imovel(
            self.empresa, self.colega, self.proprietario, codigo="COLEGA-1",
            titulo="Casa do colega", endereco=criar_endereco(self.sp, "Moema"), publicado=True,
        )

    def test_empresa_mostra_so_os_imoveis_publicados_dela_de_todas_as_cidades(self):
        response = self.pagina("empresa", self.empresa.pk)
        self.assertCountEqual(
            self.ids_site(response),
            [self.casa.pk, self.sobrado.pk, self.apto.pk, self.terreno.pk,
             self.em_campinas.pk, self.imovel_do_colega.pk],
        )
        self.assertContains(response, "Imobiliária Teste")
        self.assertContains(response, reverse("site_publico:corretor", args=[self.corretor.pk]))

        self.assertEqual(self.ids_site(self.pagina("empresa", self.outra.pk)), [self.imovel_da_outra.pk])

    def test_corretor_mostra_so_os_imoveis_dele(self):
        response = self.pagina("corretor", self.colega.pk)
        self.assertEqual(self.ids_site(response), [self.imovel_do_colega.pk])
        self.assertContains(response, "CRECI 54321-F")
        self.assertContains(response, reverse("site_publico:empresa", args=[self.empresa.pk]))

        do_corretor = self.ids_site(self.pagina("corretor", self.corretor.pk))
        self.assertNotIn(self.imovel_do_colega.pk, do_corretor)
        self.assertNotIn(self.rascunho.pk, do_corretor)
        self.assertIn(self.casa.pk, do_corretor)

    def test_empresa_inativa_da_404(self):
        self.assertEqual(
            self.pagina("empresa", self.da_inativa.empresa_id).status_code, status.HTTP_404_NOT_FOUND
        )

    def test_corretor_desativado_ou_de_empresa_inativa_da_404(self):
        self.assertEqual(
            self.pagina("corretor", self.da_inativa.corretor_responsavel_id).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.colega.is_active = False
        self.colega.save(update_fields=["is_active"])
        self.assertEqual(self.pagina("corretor", self.colega.pk).status_code, status.HTTP_404_NOT_FOUND)
        # desativado também some da lista de corretores da empresa
        self.assertNotContains(
            self.pagina("empresa", self.empresa.pk),
            reverse("site_publico:corretor", args=[self.colega.pk]),
        )

    def test_quem_nao_e_corretor_nao_tem_perfil(self):
        gestor = criar_gestor(self.empresa)
        self.assertEqual(self.pagina("corretor", gestor.pk).status_code, status.HTTP_404_NOT_FOUND)


class WhatsAppTests(SiteBase):
    """Critério 5: botão de contato pelo WhatsApp na página do imóvel."""

    def test_botao_chama_o_whatsapp_do_corretor_com_o_imovel_na_mensagem(self):
        response = self.pagina("imovel", self.casa.pk)
        self.assertContains(response, "Conversar pelo WhatsApp")
        self.assertContains(response, "https://wa.me/5511988887777?text=")
        self.assertContains(response, "Casa+com+quintal")

    def test_corretor_desativado_cai_no_whatsapp_da_empresa(self):
        self.corretor.is_active = False
        self.corretor.save(update_fields=["is_active"])
        response = self.pagina("imovel", self.casa.pk)
        self.assertContains(response, "https://wa.me/5511999999999?text=")
        self.assertNotContains(response, reverse("site_publico:corretor", args=[self.corretor.pk]))

    def test_link_whatsapp_limpa_o_numero_e_poe_o_codigo_do_brasil(self):
        self.assertTrue(link_whatsapp("(11) 98888-7777", "oi").startswith("https://wa.me/5511988887777?"))
        self.assertTrue(link_whatsapp("5511988887777", "oi").startswith("https://wa.me/5511988887777?"))
        self.assertIsNone(link_whatsapp("", "oi"))


class SemLoginESemDadoInternoTests(SiteBase):
    """Critério 6 e regra da W09: nada pede conta e nenhum dado interno aparece."""

    VALORES_PROIBIDOS = [
        # proprietário e documento
        "Dona Secreta", "17662633055", "176.626.330-55",
        # códigos internos dos imóveis
        "CASA-INTERNO", "APTO-INTERNO", "TERRENO-INTERNO", "CAMPINAS-1",
        # complemento do endereço e condomínio base do prédio
        "Casa 2", "800.00", "800,00",
        # CNPJ da empresa
        "74286058000180", "74.286.058/0001-80",
        # rascunho e imóvel de empresa inativa
        "Casa rascunho", "Casa da inativa",
    ]

    def paginas(self):
        yield self.pagina("inicio")
        yield self.pagina("vitrine", self.sp.pk)
        yield self.pagina("vitrine", self.campinas.pk)
        for imovel in (self.casa, self.sobrado, self.apto, self.terreno):
            yield self.pagina("imovel", imovel.pk)
        yield self.pagina("empresa", self.empresa.pk)
        yield self.pagina("corretor", self.corretor.pk)

    def test_nenhuma_pagina_pede_login(self):
        for response in self.paginas():
            caminho = response.request["PATH_INFO"]
            with self.subTest(pagina=caminho):
                # 200 direto: sem redirecionar para o login do painel
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertNotContains(response, 'type="password"')
                self.assertNotContains(response, reverse("contas:login"))

    def test_nenhum_dado_interno_aparece(self):
        for response in self.paginas():
            corpo = response.content.decode()
            for segredo in self.VALORES_PROIBIDOS:
                with self.subTest(pagina=response.request["PATH_INFO"], segredo=segredo):
                    self.assertNotIn(segredo, corpo)

    def test_numero_da_unidade_do_apartamento_nao_aparece(self):
        # o anúncio mostra o andar, não o número do apartamento (W09)
        self.assertNotIn("numero", self.pagina("imovel", self.apto.pk).context["imovel"]["apartamento"])
