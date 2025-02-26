from trytond.model import fields, ModelSQL, ModelView, DeactivableMixin
from trytond.pyson import Eval


class Tag(DeactivableMixin, ModelSQL, ModelView):
    'Brainbow Tag'
    __name__ = 'brainbow.tag'
    name = fields.Char('Name', required=True, translate=True)
    view = fields.Boolean('View')
    unique = fields.Boolean('Unique', states={
            'invisible': Eval('kind') != 'view',
        })
    required = fields.Boolean('Required', states={
            'invisible': Eval('kind') != 'view',
        })

