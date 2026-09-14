from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models

from core.validators import validar_imagem


class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def _criar_usuario(self, email, senha, **extra_fields):
        if not email:
            raise ValueError("O e-mail é obrigatório.")
        email = self.normalize_email(email)
        usuario = self.model(email=email, **extra_fields)
        usuario.set_password(senha)
        usuario.full_clean(exclude=["password"])
        usuario.save(using=self._db)
        return usuario

    def create_user(self, email, senha=None, **extra_fields):
        extra_fields.setdefault("perfil", Usuario.Perfil.CORRETOR)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._criar_usuario(email, senha, **extra_fields)

    def create_superuser(self, email, senha=None, **extra_fields):
        extra_fields.setdefault("perfil", Usuario.Perfil.ADMINISTRADOR)
        extra_fields["empresa"] = None
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._criar_usuario(email, senha, **extra_fields)


class Usuario(AbstractBaseUser, PermissionsMixin):
    class Perfil(models.TextChoices):
        ADMINISTRADOR = "ADMINISTRADOR", "Administrador"
        GESTOR = "GESTOR", "Gestor"
        CORRETOR = "CORRETOR", "Corretor"

    nome = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    perfil = models.CharField(max_length=20, choices=Perfil.choices)
    empresa = models.ForeignKey(
        "empresas.Empresa",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usuarios",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    # Perfil público do corretor — em branco quando o corretor é autônomo,
    # caso em que os dados vêm da própria Empresa (ver dados_perfil_publico).
    creci = models.CharField("CRECI", max_length=20, blank=True)
    foto = models.ImageField(
        upload_to="corretores/fotos/", null=True, blank=True, validators=[validar_imagem]
    )
    telefone = models.CharField(max_length=20, blank=True)
    whatsapp = models.CharField(max_length=20, blank=True)
    apresentacao = models.TextField(blank=True)

    objects = UsuarioManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["nome"]

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"
        ordering = ["nome"]

    def __str__(self):
        return f"{self.nome} ({self.get_perfil_display()})"

    def clean(self):
        super().clean()
        if self.perfil == self.Perfil.ADMINISTRADOR and self.empresa_id is not None:
            raise ValidationError({"empresa": "Administrador não pertence a nenhuma empresa."})
        if self.perfil in (self.Perfil.GESTOR, self.Perfil.CORRETOR) and self.empresa_id is None:
            raise ValidationError({"empresa": "Gestor e corretor precisam pertencer a uma empresa."})
        if self.perfil == self.Perfil.CORRETOR and self.empresa_id is not None:
            from empresas.models import Empresa

            if self.empresa.tipo == Empresa.Tipo.IMOBILIARIA:
                faltando = {}
                if not self.creci:
                    faltando["creci"] = "Corretor de imobiliária precisa do próprio CRECI."
                if not self.telefone:
                    faltando["telefone"] = "Corretor de imobiliária precisa do próprio telefone."
                if not self.whatsapp:
                    faltando["whatsapp"] = "Corretor de imobiliária precisa do próprio WhatsApp."
                if faltando:
                    raise ValidationError(faltando)

    def dados_perfil_publico(self):
        """Dados exibidos na vitrine. Corretor autônomo herda o que não preencheu da Empresa."""
        empresa = self.empresa
        return {
            "nome": self.nome,
            "email": self.email,
            "creci": self.creci or empresa.creci,
            "foto": self.foto or empresa.logomarca,
            "telefone": self.telefone or empresa.telefone,
            "whatsapp": self.whatsapp or empresa.whatsapp,
            "apresentacao": self.apresentacao or empresa.descricao,
        }
