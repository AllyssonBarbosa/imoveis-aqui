from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import Usuario


class UsuarioCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = Usuario
        fields = ("email", "nome", "perfil", "empresa")


class UsuarioChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = Usuario
        fields = ("email", "nome", "perfil", "empresa", "is_active", "is_staff")


class CorretorForm(UserCreationForm):
    """Formulário do gestor para cadastrar corretor da própria empresa.

    Não inclui `perfil` nem `empresa`: a view preenche os dois no servidor,
    então não tem como um gestor cadastrar corretor em outra empresa
    forjando o formulário.
    """

    class Meta(UserCreationForm.Meta):
        model = Usuario
        fields = (
            "email", "nome", "creci", "foto", "telefone", "whatsapp", "apresentacao",
        )

