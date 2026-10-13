from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import EmpresaOwnedModel
from core.validators import validar_imagem


class Caracteristica(models.Model):
    """Tabela geral mantida pelo administrador (portão eletrônico, ar-condicionado etc.).

    Marcável em cada imóvel — permite filtrar a vitrine por característica
    em vez de vasculhar o texto da descrição.
    """

    nome = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "característica"
        verbose_name_plural = "características"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Imovel(EmpresaOwnedModel):
    """Parte comum do cadastro do imóvel — a natureza específica vem depois.

    Nasce como rascunho: dá pra cadastrar sem endereço completo e ir
    montando aos poucos (endereço, fotos). Só pode ser publicado quando
    pode_publicar() for verdadeiro (ver W04, critério 6).
    """

    class Finalidade(models.TextChoices):
        VENDA = "VENDA", "Venda"
        ALUGUEL = "ALUGUEL", "Aluguel"
        VENDA_E_ALUGUEL = "VENDA_E_ALUGUEL", "Venda e aluguel"

    class Natureza(models.TextChoices):
        CASA = "CASA", "Casa"
        TERRENO = "TERRENO", "Terreno"

    codigo = models.CharField(max_length=30)
    titulo = models.CharField(max_length=200)
    natureza = models.CharField(max_length=20, choices=Natureza.choices)
    corretor_responsavel = models.ForeignKey(
        "contas.Usuario", on_delete=models.PROTECT, related_name="imoveis_responsavel"
    )
    proprietario = models.ForeignKey(
        "proprietarios.Proprietario", on_delete=models.PROTECT, related_name="imoveis"
    )
    finalidade = models.CharField(max_length=20, choices=Finalidade.choices)
    preco_venda = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    preco_aluguel = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    preco_condominio = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    preco_iptu = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    descricao = models.TextField(blank=True)
    caracteristicas = models.ManyToManyField(Caracteristica, blank=True, related_name="imoveis")
    endereco = models.OneToOneField(
        "core.Endereco", on_delete=models.PROTECT, null=True, blank=True, related_name="imovel"
    )
    publicado = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "imóvel"
        verbose_name_plural = "imóveis"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "codigo"],
                name="codigo_unico_por_empresa",
                violation_error_message="Já existe um imóvel com esse código nesta empresa.",
            )
        ]

    def __str__(self):
        return f"{self.codigo} — {self.titulo}"

    def clean(self):
        super().clean()

        # self.empresa_id só está preenchido depois que a view define a empresa
        # (o formulário não tem esse campo) — sem empresa ainda, não dá pra
        # comparar, então esses dois cruzamentos só rodam quando ela existe.
        if self.corretor_responsavel_id:
            from contas.models import Usuario

            if self.empresa_id and self.corretor_responsavel.empresa_id != self.empresa_id:
                raise ValidationError(
                    {"corretor_responsavel": "O corretor responsável precisa ser da mesma empresa."}
                )
            if self.corretor_responsavel.perfil != Usuario.Perfil.CORRETOR:
                raise ValidationError(
                    {"corretor_responsavel": "O responsável precisa ter perfil de corretor."}
                )

        if self.proprietario_id and self.empresa_id and self.proprietario.empresa_id != self.empresa_id:
            raise ValidationError({"proprietario": "O proprietário precisa ser da mesma empresa."})

        precisa_de_venda = self.finalidade in (self.Finalidade.VENDA, self.Finalidade.VENDA_E_ALUGUEL)
        precisa_de_aluguel = self.finalidade in (self.Finalidade.ALUGUEL, self.Finalidade.VENDA_E_ALUGUEL)

        if precisa_de_venda and not self.preco_venda:
            raise ValidationError({"preco_venda": "Finalidade de venda exige o preço de venda."})
        if precisa_de_aluguel and not self.preco_aluguel:
            raise ValidationError({"preco_aluguel": "Finalidade de aluguel exige o valor do aluguel."})

    @property
    def detalhes(self):
        """Dados próprios da natureza (Casa ou Terreno) — None enquanto não preenchidos."""
        relacao = {self.Natureza.CASA: "casa", self.Natureza.TERRENO: "terreno"}.get(self.natureza)
        if relacao is None:
            return None
        try:
            return getattr(self, relacao)
        except ObjectDoesNotExist:
            return None

    def pode_publicar(self):
        return bool(
            self.endereco_id
            and self.endereco.latitude is not None
            and self.endereco.longitude is not None
        )

    def publicar(self):
        if not self.pode_publicar():
            raise ValidationError(
                "Imóvel sem cidade ou sem coordenada não pode ser publicado."
            )
        self.publicado = True
        self.save(update_fields=["publicado"])


class FotoImovel(models.Model):
    """Foto do imóvel, com ordem de exibição e uma marcação de capa."""

    imovel = models.ForeignKey(Imovel, on_delete=models.CASCADE, related_name="fotos")
    imagem = models.ImageField(upload_to="imoveis/fotos/", validators=[validar_imagem])
    ordem = models.PositiveIntegerField(default=0)
    capa = models.BooleanField(default=False)

    class Meta:
        verbose_name = "foto do imóvel"
        verbose_name_plural = "fotos do imóvel"
        ordering = ["ordem", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["imovel"],
                condition=models.Q(capa=True),
                name="uma_capa_por_imovel",
                violation_error_message="Este imóvel já tem uma foto de capa.",
            )
        ]

    def __str__(self):
        return f"Foto {self.ordem} de {self.imovel}"

    def definir_como_capa(self):
        FotoImovel.objects.filter(imovel_id=self.imovel_id, capa=True).exclude(pk=self.pk).update(
            capa=False
        )
        self.capa = True
        self.save(update_fields=["capa"])


def _area_positiva():
    return [MinValueValidator(Decimal("0.01"), "A área precisa ser maior que zero.")]


def _medida_positiva():
    return [MinValueValidator(Decimal("0.01"), "A medida precisa ser maior que zero.")]


class Casa(models.Model):
    """Dados próprios do imóvel de natureza casa (W05).

    A área livre não tem coluna: é sempre área do terreno − área construída.
    Se as três fossem digitadas, poderiam discordar entre si.
    """

    imovel = models.OneToOneField(Imovel, on_delete=models.CASCADE, related_name="casa")
    area_terreno = models.DecimalField(
        "área do terreno (m²)", max_digits=10, decimal_places=2, validators=_area_positiva()
    )
    area_construida = models.DecimalField(
        "área construída (m²)", max_digits=10, decimal_places=2, validators=_area_positiva()
    )
    quartos = models.PositiveSmallIntegerField(default=0)
    suites = models.PositiveSmallIntegerField("suítes", default=0)
    banheiros = models.PositiveSmallIntegerField(default=0)
    salas = models.PositiveSmallIntegerField(default=0)
    vagas = models.PositiveSmallIntegerField(default=0)
    pavimentos = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    ano_construcao = models.PositiveSmallIntegerField(
        "ano de construção", null=True, blank=True, validators=[MinValueValidator(1800)]
    )
    piscina = models.BooleanField(default=False)
    quintal = models.BooleanField(default=False)
    churrasqueira = models.BooleanField(default=False)
    mobiliada = models.BooleanField(default=False)

    class Meta:
        verbose_name = "casa"
        verbose_name_plural = "casas"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(area_construida__lte=models.F("area_terreno")),
                name="casa_area_construida_ate_area_terreno",
                violation_error_message="A área construída não pode ser maior que a área do terreno.",
            )
        ]

    def __str__(self):
        return f"Casa de {self.imovel}"

    @property
    def area_livre(self):
        if self.area_terreno is None or self.area_construida is None:
            return None
        return self.area_terreno - self.area_construida

    def clean(self):
        super().clean()

        if self.imovel_id and self.imovel.natureza != Imovel.Natureza.CASA:
            raise ValidationError("Este imóvel não é uma casa.")

        if (
            self.area_terreno is not None
            and self.area_construida is not None
            and self.area_construida > self.area_terreno
        ):
            raise ValidationError({
                "area_construida": (
                    f"A área construída ({self.area_construida} m²) não pode ser maior "
                    f"que a área do terreno ({self.area_terreno} m²)."
                )
            })

        if self.suites is not None and self.quartos is not None and self.suites > self.quartos:
            raise ValidationError({"suites": "O número de suítes não pode passar o de quartos."})

        if self.ano_construcao and self.ano_construcao > timezone.now().year:
            raise ValidationError({"ano_construcao": "O ano de construção não pode estar no futuro."})


class Terreno(models.Model):
    """Dados próprios do imóvel de natureza terreno livre (W05)."""

    class Topografia(models.TextChoices):
        PLANO = "PLANO", "Plano"
        ACLIVE = "ACLIVE", "Aclive"
        DECLIVE = "DECLIVE", "Declive"

    class UsoPermitido(models.TextChoices):
        RESIDENCIAL = "RESIDENCIAL", "Residencial"
        COMERCIAL = "COMERCIAL", "Comercial"
        MISTO = "MISTO", "Misto"
        INDUSTRIAL = "INDUSTRIAL", "Industrial"
        RURAL = "RURAL", "Rural"

    imovel = models.OneToOneField(Imovel, on_delete=models.CASCADE, related_name="terreno")
    area_total = models.DecimalField(
        "área total (m²)", max_digits=10, decimal_places=2, validators=_area_positiva()
    )
    frente = models.DecimalField("frente (m)", max_digits=8, decimal_places=2, validators=_medida_positiva())
    fundo = models.DecimalField("fundo (m)", max_digits=8, decimal_places=2, validators=_medida_positiva())
    lateral_esquerda = models.DecimalField(
        "lateral esquerda (m)", max_digits=8, decimal_places=2, validators=_medida_positiva()
    )
    lateral_direita = models.DecimalField(
        "lateral direita (m)", max_digits=8, decimal_places=2, validators=_medida_positiva()
    )
    topografia = models.CharField(max_length=10, choices=Topografia.choices)
    murado = models.BooleanField(default=False)
    esquina = models.BooleanField("de esquina", default=False)
    uso_permitido = models.CharField(max_length=20, choices=UsoPermitido.choices)

    class Meta:
        verbose_name = "terreno"
        verbose_name_plural = "terrenos"

    def __str__(self):
        return f"Terreno de {self.imovel}"

    def clean(self):
        super().clean()
        if self.imovel_id and self.imovel.natureza != Imovel.Natureza.TERRENO:
            raise ValidationError("Este imóvel não é um terreno.")
