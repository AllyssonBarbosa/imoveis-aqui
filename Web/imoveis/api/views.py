from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import EhCorretor
from localizacao.models import Cidade

from ..models import Caracteristica, FotoImovel, Imovel, Predio
from ..vitrine import imoveis_da_vitrine
from .serializers import (
    SERIALIZER_POR_NATUREZA,
    CaracteristicaSerializer,
    EnderecoSerializer,
    FotoImovelSerializer,
    ImovelSerializer,
    ImovelVitrineDetalheSerializer,
    ImovelVitrineSerializer,
    PredioSerializer,
)


class CaracteristicaViewSet(viewsets.ReadOnlyModelViewSet):
    """Lista de leitura — quem mantém é o administrador, pelo Django Admin."""

    serializer_class = CaracteristicaSerializer
    permission_classes = [EhCorretor]
    queryset = Caracteristica.objects.all()


class ImovelViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = ImovelSerializer
    permission_classes = [EhCorretor]
    queryset = Imovel.objects.select_related(
        "endereco", "casa", "terreno", "apartamento__predio__endereco"
    )

    @action(detail=True, methods=["get", "put"])
    def endereco(self, request, pk=None):
        imovel = self.get_object()

        if request.method == "GET":
            if not imovel.endereco_do_anuncio:
                return Response(None)
            return Response(EnderecoSerializer(imovel.endereco_do_anuncio).data)

        if imovel.natureza == Imovel.Natureza.APARTAMENTO:
            return Response(
                {"detail": "O endereço do apartamento é o do prédio — altere pelo cadastro do prédio."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = EnderecoSerializer(instance=imovel.endereco, data=request.data)
        serializer.is_valid(raise_exception=True)
        endereco = serializer.save()
        if not imovel.endereco_id:
            imovel.endereco = endereco
            imovel.save(update_fields=["endereco"])
        return Response(EnderecoSerializer(endereco).data)

    @action(detail=True, methods=["get", "put"])
    def detalhes(self, request, pk=None):
        """Dados próprios da natureza: os campos de casa ou de terreno, conforme o imóvel."""
        imovel = self.get_object()
        serializer_class = SERIALIZER_POR_NATUREZA[imovel.natureza]

        if request.method == "GET":
            if imovel.detalhes is None:
                return Response(None)
            return Response(serializer_class(imovel.detalhes).data)

        serializer = serializer_class(
            instance=imovel.detalhes, data=request.data, context={"imovel": imovel}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def publicar(self, request, pk=None):
        imovel = self.get_object()
        try:
            imovel.publicar()
        except DjangoValidationError as erro:
            return Response({"detail": erro.messages[0]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(imovel).data)


class PredioViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    """Prédios da própria empresa. Alterar aqui reflete em todos os apartamentos ligados."""

    serializer_class = PredioSerializer
    permission_classes = [EhCorretor]
    queryset = Predio.objects.select_related("endereco")

    def destroy(self, request, *args, **kwargs):
        predio = self.get_object()
        if predio.apartamentos.exists():
            return Response(
                {"detail": "Este prédio tem apartamentos ligados e não pode ser excluído."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        endereco = predio.endereco
        predio.delete()
        endereco.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FotoImovelViewSet(viewsets.ModelViewSet):
    """Fotos do imóvel — filtradas por ?imovel=<id>. Aceita várias de uma vez."""

    serializer_class = FotoImovelSerializer
    permission_classes = [EhCorretor]
    queryset = FotoImovel.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset().filter(imovel__empresa=self.request.user.empresa)
        imovel_id = self.request.query_params.get("imovel")
        if imovel_id:
            queryset = queryset.filter(imovel_id=imovel_id)
        return queryset

    def create(self, request, *args, **kwargs):
        imovel = get_object_or_404(
            Imovel, pk=request.data.get("imovel"), empresa=request.user.empresa
        )
        arquivos = request.FILES.getlist("imagens") or request.FILES.getlist("imagem")
        if not arquivos:
            return Response({"imagens": ["Envie ao menos uma imagem."]}, status=status.HTTP_400_BAD_REQUEST)

        proxima_ordem = imovel.fotos.count()
        criadas = []
        try:
            for indice, arquivo in enumerate(arquivos):
                foto = FotoImovel(imovel=imovel, imagem=arquivo, ordem=proxima_ordem + indice)
                foto.full_clean()
                foto.save()
                criadas.append(foto)
        except DjangoValidationError as erro:
            return Response({"imagens": erro.messages}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(criadas, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def definir_capa(self, request, pk=None):
        foto = self.get_object()
        foto.definir_como_capa()
        return Response(self.get_serializer(foto).data)


# --- Vitrine pública (W09) ---------------------------------------------------


class PaginacaoVitrine(PageNumberPagination):
    page_size = 20


ORDENACOES_VITRINE = {
    "recentes": [F("criado_em").desc()],
    "preco": [F("preco_anuncio").asc(nulls_last=True)],
    "-preco": [F("preco_anuncio").desc(nulls_last=True)],
    "area": [F("area_anuncio").asc(nulls_last=True)],
    "-area": [F("area_anuncio").desc(nulls_last=True)],
}


def _numero(params, nome, tipo=Decimal):
    """Lê um filtro numérico da URL — valor inválido vira 400 com mensagem clara."""
    valor = params.get(nome)
    if valor in (None, ""):
        return None
    try:
        numero = tipo(valor)
    except (InvalidOperation, ValueError):
        raise ValidationError({nome: "Informe um número válido."})
    if numero < 0:
        raise ValidationError({nome: "O valor não pode ser negativo."})
    return numero


def _escolha(params, nome, opcoes):
    valor = params.get(nome)
    if valor in (None, ""):
        return None
    if valor not in opcoes:
        raise ValidationError({nome: f"Opções válidas: {', '.join(opcoes)}."})
    return valor


class VitrineCidadeAPIView(generics.ListAPIView):
    """Busca da vitrine, sempre dentro de uma cidade. Pública: sem login e sem token.

    authentication_classes vazio: nem um token vencido no app impede a consulta.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = ImovelVitrineSerializer
    pagination_class = PaginacaoVitrine

    def get_queryset(self):
        cidade = get_object_or_404(Cidade, pk=self.kwargs["cidade_id"])
        params = self.request.query_params
        queryset = imoveis_da_vitrine().filter(cidade_anuncio=cidade.pk)

        busca = params.get("busca", "").strip()
        if busca:
            queryset = queryset.filter(
                Q(titulo__icontains=busca)
                | Q(bairro_anuncio__icontains=busca)
                | Q(descricao__icontains=busca)
            )

        # "venda" também traz quem é "venda e aluguel" (e o mesmo pro aluguel)
        finalidade = _escolha(params, "finalidade", Imovel.Finalidade.values)
        if finalidade:
            queryset = queryset.filter(
                finalidade__in=[finalidade, Imovel.Finalidade.VENDA_E_ALUGUEL]
            )

        natureza = _escolha(params, "natureza", Imovel.Natureza.values)
        if natureza:
            queryset = queryset.filter(natureza=natureza)

        # o preço comparado depende da finalidade buscada; sem finalidade, vale o que houver
        if finalidade == Imovel.Finalidade.VENDA:
            preco = F("preco_venda")
        elif finalidade == Imovel.Finalidade.ALUGUEL:
            preco = F("preco_aluguel")
        else:
            preco = Coalesce("preco_venda", "preco_aluguel")
        queryset = queryset.annotate(preco_anuncio=preco)

        faixas = {
            "preco_min": "preco_anuncio__gte",
            "preco_max": "preco_anuncio__lte",
            "area_min": "area_anuncio__gte",
            "area_max": "area_anuncio__lte",
        }
        for nome, lookup in faixas.items():
            valor = _numero(params, nome)
            if valor is not None:
                queryset = queryset.filter(**{lookup: valor})

        # quartos/suítes/vagas são "no mínimo" — quem pede 2 quartos aceita 3
        minimos = {"quartos": "quartos_anuncio__gte", "suites": "suites_anuncio__gte", "vagas": "vagas_anuncio__gte"}
        for nome, lookup in minimos.items():
            valor = _numero(params, nome, tipo=int)
            if valor is not None:
                queryset = queryset.filter(**{lookup: valor})

        bairro = params.get("bairro", "").strip()
        if bairro:
            queryset = queryset.filter(bairro_anuncio__iexact=bairro)

        # ?caracteristicas=1,3 — o imóvel precisa ter todas as escolhidas
        caracteristicas = params.get("caracteristicas", "").strip()
        if caracteristicas:
            try:
                ids = {int(item) for item in caracteristicas.split(",") if item.strip()}
            except ValueError:
                raise ValidationError({"caracteristicas": "Informe os ids separados por vírgula."})
            for caracteristica_id in ids:
                queryset = queryset.filter(caracteristicas=caracteristica_id)

        ordenar = _escolha(params, "ordenar", list(ORDENACOES_VITRINE)) or "recentes"
        # o id no fim deixa a ordem estável entre as páginas quando há empate
        return (
            queryset.order_by(*ORDENACOES_VITRINE[ordenar], "-id")
            .distinct()
            .prefetch_related("fotos")
        )


class ImovelVitrineDetalheAPIView(generics.RetrieveAPIView):
    """Página pública do imóvel. Rascunho ou empresa inativa cai em 404."""

    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = ImovelVitrineDetalheSerializer

    def get_queryset(self):
        return imoveis_da_vitrine().select_related(
            "endereco__cidade", "corretor_responsavel__empresa", "casa", "terreno",
            "apartamento__predio__endereco__cidade",
        ).prefetch_related("fotos", "caracteristicas")
