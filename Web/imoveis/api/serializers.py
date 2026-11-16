from django.core.exceptions import NON_FIELD_ERRORS, ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from rest_framework.settings import api_settings

from contas.api.serializers import CorretorPublicoSerializer
from core.models import Endereco
from empresas.api.serializers import CidadeSerializer

from ..models import Apartamento, Caracteristica, Casa, FotoImovel, Imovel, Predio, Terreno


class CaracteristicaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Caracteristica
        fields = ["id", "nome"]


class EnderecoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Endereco
        fields = [
            "id", "cep", "logradouro", "numero", "complemento", "bairro",
            "cidade", "latitude", "longitude",
        ]


def _erros_do_model(erro):
    """Converte o ValidationError do Django pro formato do DRF (__all__ vira non_field_errors)."""
    erros = dict(erro.message_dict)
    if NON_FIELD_ERRORS in erros:
        erros[api_settings.NON_FIELD_ERRORS_KEY] = erros.pop(NON_FIELD_ERRORS)
    return serializers.ValidationError(erros)


class FotoImovelSerializer(serializers.ModelSerializer):
    class Meta:
        model = FotoImovel
        fields = ["id", "imovel", "imagem", "ordem", "capa"]
        read_only_fields = ["id", "imovel", "capa"]


class DetalhesSerializerBase(serializers.ModelSerializer):
    """Valida pelo full_clean do model — a regra fica num lugar só (models.py).

    Espera o imóvel em context["imovel"]; ele não é campo editável.
    """

    def validate(self, attrs):
        instancia = self.instance or self.Meta.model(imovel=self.context["imovel"])
        for campo, valor in attrs.items():
            setattr(instancia, campo, valor)
        try:
            instancia.full_clean(exclude=["imovel"])
        except DjangoValidationError as erro:
            raise _erros_do_model(erro)
        return attrs

    def create(self, validated_data):
        return self.Meta.model.objects.create(imovel=self.context["imovel"], **validated_data)


class PredioSerializer(serializers.ModelSerializer):
    """Prédio com o endereço aninhado — cadastrado uma vez, lido por todas as unidades."""

    endereco = EnderecoSerializer()
    total_apartamentos = serializers.IntegerField(source="apartamentos.count", read_only=True)

    class Meta:
        model = Predio
        fields = [
            "id", "nome", "endereco", "andares", "unidades_por_andar", "ano_construcao",
            "elevadores", "portaria_24h", "piscina", "academia", "salao_de_festas",
            "playground", "valor_condominio_base", "total_apartamentos",
        ]
        # a unicidade (empresa, nome) é checada no full_clean, com a mensagem em português
        validators = []

    def validate(self, attrs):
        instancia = self.instance or Predio()
        for campo, valor in attrs.items():
            if campo != "endereco":
                setattr(instancia, campo, valor)
        instancia.empresa = self.context["request"].user.empresa
        try:
            instancia.full_clean(exclude=["endereco"])
        except DjangoValidationError as erro:
            raise _erros_do_model(erro)
        return attrs

    def create(self, validated_data):
        dados_endereco = validated_data.pop("endereco")
        with transaction.atomic():
            endereco = Endereco.objects.create(**dados_endereco)
            return Predio.objects.create(endereco=endereco, **validated_data)

    def update(self, instance, validated_data):
        dados_endereco = validated_data.pop("endereco", None)
        with transaction.atomic():
            if dados_endereco is not None:
                for campo, valor in dados_endereco.items():
                    setattr(instance.endereco, campo, valor)
                instance.endereco.save()
            for campo, valor in validated_data.items():
                setattr(instance, campo, valor)
            instance.save()
        return instance


class CasaSerializer(DetalhesSerializerBase):
    area_livre = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Casa
        fields = [
            "area_terreno", "area_construida", "area_livre", "quartos", "suites", "banheiros",
            "salas", "vagas", "pavimentos", "ano_construcao", "piscina", "quintal",
            "churrasqueira", "mobiliada",
        ]


class TerrenoSerializer(DetalhesSerializerBase):
    class Meta:
        model = Terreno
        fields = [
            "area_total", "frente", "fundo", "lateral_esquerda", "lateral_direita",
            "topografia", "murado", "esquina", "uso_permitido",
        ]


class ApartamentoSerializer(DetalhesSerializerBase):
    """Na escrita, `predio` é o id; na leitura, vem o prédio inteiro (endereço, lazer...)."""

    class Meta:
        model = Apartamento
        fields = [
            "predio", "andar", "numero", "area_privativa", "area_total", "quartos", "suites",
            "banheiros", "vagas", "varanda", "posicao_solar",
        ]
        # andar + número repetido no prédio é checado no full_clean, com a mensagem em português
        validators = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        imovel = self.context.get("imovel")
        if imovel is not None:
            # prédio de outra empresa cai em "não existe", sem revelar que existe
            self.fields["predio"].queryset = Predio.objects.filter(empresa=imovel.empresa)

    def to_representation(self, instancia):
        dados = super().to_representation(instancia)
        dados["predio"] = PredioSerializer(instancia.predio).data
        return dados


SERIALIZER_POR_NATUREZA = {
    Imovel.Natureza.CASA: CasaSerializer,
    Imovel.Natureza.TERRENO: TerrenoSerializer,
    Imovel.Natureza.APARTAMENTO: ApartamentoSerializer,
}


class ImovelSerializer(serializers.ModelSerializer):
    # apartamento mostra o endereço do prédio
    endereco = EnderecoSerializer(source="endereco_do_anuncio", read_only=True)
    casa = serializers.SerializerMethodField()
    terreno = serializers.SerializerMethodField()
    apartamento = serializers.SerializerMethodField()
    fotos = FotoImovelSerializer(many=True, read_only=True)
    pode_publicar = serializers.BooleanField(read_only=True)

    class Meta:
        model = Imovel
        fields = [
            "id", "codigo", "titulo", "natureza", "corretor_responsavel", "proprietario", "finalidade",
            "preco_venda", "preco_aluguel", "preco_condominio", "preco_iptu", "descricao",
            "caracteristicas", "casa", "terreno", "apartamento", "endereco", "fotos", "publicado", "pode_publicar", "criado_em",
        ]
        read_only_fields = ["id", "publicado", "endereco", "fotos", "pode_publicar", "criado_em"]

    def _detalhes_se_for(self, imovel, natureza):
        if imovel.natureza != natureza or imovel.detalhes is None:
            return None
        return SERIALIZER_POR_NATUREZA[natureza](imovel.detalhes).data

    def get_casa(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.CASA)

    def get_terreno(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.TERRENO)

    def get_apartamento(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.APARTAMENTO)

    def validate_natureza(self, valor):
        if self.instance and valor != self.instance.natureza:
            raise serializers.ValidationError("A natureza não pode ser trocada depois do cadastro.")
        return valor

    def validate(self, attrs):
        request = self.context["request"]
        instancia = self.instance or Imovel()
        for campo, valor in attrs.items():
            if campo == "caracteristicas":
                continue
            setattr(instancia, campo, valor)
        instancia.empresa = request.user.empresa

        try:
            instancia.full_clean(exclude=["endereco"])
        except DjangoValidationError as erro:
            raise serializers.ValidationError(erro.message_dict)
        return attrs

    def create(self, validated_data):
        caracteristicas = validated_data.pop("caracteristicas", [])
        imovel = Imovel.objects.create(**validated_data)
        imovel.caracteristicas.set(caracteristicas)
        return imovel

    def update(self, instance, validated_data):
        caracteristicas = validated_data.pop("caracteristicas", None)
        for campo, valor in validated_data.items():
            setattr(instance, campo, valor)
        instance.save()
        if caracteristicas is not None:
            instance.caracteristicas.set(caracteristicas)
        return instance


# --- Vitrine pública (W09) ---------------------------------------------------
# Serializers só de leitura, com os campos do anúncio listados um a um.
# Nada de proprietário, código interno, empresa ou status de publicação:
# campo novo no model só aparece aqui se alguém o colocar de propósito.


class EnderecoPublicoSerializer(serializers.ModelSerializer):
    """Sem complemento (apto/bloco) — o resto é o que o mapa e o anúncio precisam."""

    cidade = CidadeSerializer(read_only=True)

    class Meta:
        model = Endereco
        fields = ["logradouro", "numero", "bairro", "cep", "cidade", "latitude", "longitude"]


class FotoPublicaSerializer(serializers.ModelSerializer):
    class Meta:
        model = FotoImovel
        fields = ["imagem", "ordem", "capa"]


class CasaPublicaSerializer(serializers.ModelSerializer):
    area_livre = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Casa
        fields = [
            "area_terreno", "area_construida", "area_livre", "quartos", "suites", "banheiros",
            "salas", "vagas", "pavimentos", "ano_construcao", "piscina", "quintal",
            "churrasqueira", "mobiliada",
        ]


class TerrenoPublicoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Terreno
        fields = [
            "area_total", "frente", "fundo", "lateral_esquerda", "lateral_direita",
            "topografia", "murado", "esquina", "uso_permitido",
        ]


class PredioPublicoSerializer(serializers.ModelSerializer):
    """O que o prédio mostra no anúncio — o endereço já sai no próprio imóvel."""

    class Meta:
        model = Predio
        fields = [
            "nome", "andares", "ano_construcao", "elevadores", "portaria_24h",
            "piscina", "academia", "salao_de_festas", "playground",
        ]


class ApartamentoPublicoSerializer(serializers.ModelSerializer):
    """Sem o número da unidade — o andar basta pro anúncio."""

    predio = PredioPublicoSerializer(read_only=True)

    class Meta:
        model = Apartamento
        fields = [
            "predio", "andar", "area_privativa", "area_total", "quartos", "suites",
            "banheiros", "vagas", "varanda", "posicao_solar",
        ]


SERIALIZER_PUBLICO_POR_NATUREZA = {
    Imovel.Natureza.CASA: CasaPublicaSerializer,
    Imovel.Natureza.TERRENO: TerrenoPublicoSerializer,
    Imovel.Natureza.APARTAMENTO: ApartamentoPublicoSerializer,
}


class ImovelVitrineSerializer(serializers.ModelSerializer):
    """Cartão do imóvel na busca — os números vêm das anotações de imoveis_da_vitrine()."""

    bairro = serializers.CharField(source="bairro_anuncio", read_only=True)
    area = serializers.DecimalField(
        source="area_anuncio", max_digits=10, decimal_places=2, read_only=True
    )
    quartos = serializers.IntegerField(source="quartos_anuncio", read_only=True)
    suites = serializers.IntegerField(source="suites_anuncio", read_only=True)
    vagas = serializers.IntegerField(source="vagas_anuncio", read_only=True)
    foto_capa = serializers.SerializerMethodField()

    class Meta:
        model = Imovel
        fields = [
            "id", "titulo", "natureza", "finalidade", "preco_venda", "preco_aluguel",
            "preco_condominio", "bairro", "area", "quartos", "suites", "vagas", "foto_capa",
        ]

    def get_foto_capa(self, imovel):
        # as fotos já vêm do prefetch, ordenadas por ordem — sem capa marcada, vale a primeira
        fotos = list(imovel.fotos.all())
        if not fotos:
            return None
        foto = next((f for f in fotos if f.capa), fotos[0])
        request = self.context.get("request")
        return request.build_absolute_uri(foto.imagem.url) if request else foto.imagem.url


class ImovelVitrineDetalheSerializer(serializers.ModelSerializer):
    """Página do imóvel: fotos, dados da natureza, endereço com coordenada e corretor."""

    caracteristicas = CaracteristicaSerializer(many=True, read_only=True)
    # apartamento mostra o endereço do prédio
    endereco = EnderecoPublicoSerializer(source="endereco_do_anuncio", read_only=True)
    casa = serializers.SerializerMethodField()
    terreno = serializers.SerializerMethodField()
    apartamento = serializers.SerializerMethodField()
    fotos = FotoPublicaSerializer(many=True, read_only=True)
    corretor_responsavel = serializers.SerializerMethodField()

    class Meta:
        model = Imovel
        fields = [
            "id", "titulo", "natureza", "finalidade", "preco_venda", "preco_aluguel",
            "preco_condominio", "preco_iptu", "descricao", "caracteristicas", "casa",
            "terreno", "apartamento", "endereco", "fotos", "corretor_responsavel",
        ]

    def _detalhes_se_for(self, imovel, natureza):
        if imovel.natureza != natureza or imovel.detalhes is None:
            return None
        return SERIALIZER_PUBLICO_POR_NATUREZA[natureza](imovel.detalhes, context=self.context).data

    def get_casa(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.CASA)

    def get_terreno(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.TERRENO)

    def get_apartamento(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.APARTAMENTO)

    def get_corretor_responsavel(self, imovel):
        # corretor desativado não recebe contato (W02) — o imóvel continua, sem o contato dele
        corretor = imovel.corretor_responsavel
        if not corretor.is_active:
            return None
        return CorretorPublicoSerializer(corretor, context=self.context).data
