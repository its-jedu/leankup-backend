import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

class ComplexPasswordValidator:
    """
    Validate that password contains at least one letter, one number, and one special character.
    """
    def validate(self, password, user=None):
        if not re.findall(r'[A-Za-z]', password):
            raise ValidationError(
                _("Password must contain at least one letter."),
                code='password_no_letter',
            )
        if not re.findall(r'\d', password):
            raise ValidationError(
                _("Password must contain at least one number."),
                code='password_no_number',
            )
        if not re.findall(r'[!@#$%^&*(),.?":{}|<>]', password):
            raise ValidationError(
                _("Password must contain at least one special character."),
                code='password_no_special',
            )

    def get_help_text(self):
        return _(
            "Your password must contain at least one letter, one number, and one special character."
        )