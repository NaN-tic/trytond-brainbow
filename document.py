import logging
from openai import OpenAI, OpenAIError
from sql.conditionals import Coalesce
from sql.operators import Equal

from trytond.pool import Pool, PoolMeta
from trytond.model import DeactivableMixin, Exclude, fields, ModelSQL, ModelView, Unique, tree
from trytond.pyson import Bool, Eval
from trytond.exceptions import UserError
from trytond.i18n import gettext
from trytond.config import config
from trytond.transaction import Transaction

logger = logging.getLogger(__name__)

GPT_MODEL, GPT_MODEL_LOW = 'gpt-4o', 'gpt-4o-mini'
OPENAI_KEY = config.get('openai', 'api_key')
OPENAI_ORGANIZATION = config.get('openai', 'organization')
OPENAI_URL = config.get('openai', 'url')
if OPENAI_KEY:
    CLIENT = OpenAI(api_key=OPENAI_KEY, organization=OPENAI_ORGANIZATION, base_url=OPENAI_URL)
else:
    CLIENT = None

def split_markdown_paragraphs(text):
    paragraphs = []
    lines = text.split('\n')
    current_paragraph = ''

    for line in lines:
        line = line.strip()

        if not line and current_paragraph:
            paragraphs.append(current_paragraph.strip())
            current_paragraph = ''

        elif line.startswith(('- ', '* ', '+ ')) or (
            len(line.split('.')) >= 2 and line.split('.')[0].isdigit() and line.split('.')[1].startswith(' ')):
            if current_paragraph:
                paragraphs.append(current_paragraph.strip())

            item = line.lstrip('-*+0123456789. ').strip()
            if item:
                paragraphs.append(item)
            current_paragraph = ''

        elif line:
            current_paragraph += line + ' '

    if current_paragraph:
        paragraphs.append(current_paragraph.strip())

    return paragraphs


class Index(metaclass=PoolMeta):
    __name__ = 'kb.index'

    @classmethod
    def _get_resources(cls):
        return super()._get_resources() + [
            'brainbow.document']


class Tag(DeactivableMixin, tree(separator=" / "), ModelSQL, ModelView):
    'Brainbow Tag'
    __name__ = 'brainbow.tag'
    name = fields.Char('Name', required=True, translate=True)
    view = fields.Boolean('View')
    unique = fields.Boolean('Unique', states={
            'invisible': ~Bool(Eval('view')),
            })
    required = fields.Boolean('Required', states={
            'invisible': ~Bool(Eval('view')),
            })
    parent = fields.Many2One('brainbow.tag', "Parent")
    children = fields.One2Many('brainbow.tag', 'parent', "Children")
    description = fields.Text('Description')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints = [
            ('name_parent_exclude',
                Exclude(t, (t.name, Equal), (Coalesce(t.parent, -1), Equal)),
                'brainbow.msg_tag_name_unique'),
            ]
        cls._order.insert(0, ('name', 'ASC'))


class Document(DeactivableMixin, ModelSQL, ModelView):
    'Brainbow Document'
    __name__ = 'brainbow.document'

    name = fields.Char('Name', required=True)
    text = fields.Text('Text')
    language = fields.Many2One('ir.lang', 'Language')
    resource = fields.Reference('Resource', selection='get_models')
    reader_groups = fields.Many2Many('brainbow.document-reader-group',
        'document', 'reader_group', 'Reader Groups')
    writer_groups = fields.Many2Many('brainbow.document-writer-group',
        'document', 'writer_group', 'Writer Groups')
    tags_char = fields.Function(fields.Char('Tags'), 'get_tags_char',
        searcher='search_tags_char')
    tags = fields.Many2Many('brainbow.document-tag', 'document', 'tag', 'Tags',
        domain=[
            ('view', '=', False),
            ])

    def get_tags_char(self, name):
        return ', '.join([tag.rec_name for tag in self.tags])

    @classmethod
    def search_tags_char(cls, name, clause):
        return [('tags.name',) + tuple(clause[1:])]

    @classmethod
    def search_rec_name(cls, name, clause):
        return ['OR',
            ('name',) + tuple(clause[1:]),
            ('text',) + tuple(clause[1:]),
            ]

    @classmethod
    def create(cls, vlist):
        documents = super().create(vlist)
        cls.analyze(documents)
        return documents

    @staticmethod
    def get_models():
        pool = Pool()
        Model = pool.get('ir.model')
        ModelAccess = pool.get('ir.model.access')
        models = Model.get_name_items()
        if Transaction().check_access:
            access = ModelAccess.get_access([m for m, _ in models])
            models = [(m, n) for m, n in models if access[m]['read']]
        return [(None, ''),] + models

    def set_name(self):
        if self.name:
            return
        if not CLIENT:
            logger.error("OpenAI is not configured.")
            return
        try:
            response = CLIENT.chat.completions.create(
                model=GPT_MODEL_LOW,
                temperature=0,
                messages=[{
                    "role": "developer",
                    "content": (
                        "Create a unique and relevant title based on the user's message. Use initials or acronyms when appropiate."
                        "Example outputs:"
                        "\nSearching for vacation cities"
                        "\nCreate parties"
                        "\nQA about Tryton"
                    )
                }, {
                    'role': 'user',
                    'content': self.text,
                }]
            )
            self.name = response.choices[0].message.content
        except OpenAIError:
            logger.exception("While setting a name on a conversation")

    def set_language(self):
        Lang = Pool().get('ir.lang')

        if self.language:
            return
        if not CLIENT:
            logger.error("OpenAI is not configured.")
            return
        try:
            # Use chat completions to detect language.
            response = CLIENT.chat.completions.create(
                model=GPT_MODEL_LOW,
                temperature=0,
                messages=[{
                    "role": "developer",
                    "content": (
                        "Detect the language of the user's message. Output the detected language code. "
                        "Example outputs:"
                        "\nca"
                        "\nen"
                        "\nes"
                    )
                }, {
                    'role': 'user',
                    'content': self.text,
                }]
            )
            lang = response.choices[0].message.content
            languages = Lang.search([
                    ('code', '=', lang),
                    ], limit=1)
            if languages:
                self.language = languages[0]
        except OpenAIError:
            logger.exception("While setting a language on a conversation")

    @classmethod
    def analyze(cls, documents):
        for document in documents:
            document.set_name()
            document.set_language()

    @fields.depends('name', 'language', 'text')
    def on_change_text(self):
        self.set_name()
        self.set_language()

    @classmethod
    def validate_fields(cls, documents, field_names):
        super().validate_fields(documents, field_names)
        cls._check_tags(documents, field_names)
        cls.indexate(documents, field_names)

    @classmethod
    def indexate(cls, documents, field_names=None):
        Index = Pool().get('kb.index')

        if field_names and not field_names & {'text', 'language'}:
            return
        all_indexes = {}
        for document in documents:
            indexes = []
            for paragraph in split_markdown_paragraphs(document.text):
                indexes.append(Index(text=paragraph,
                    language_code=document.language.code,
                    resource=document))
            all_indexes[document] = indexes
        Index.compute_indexes(all_indexes)

    @classmethod
    def _check_tags(cls, documents, field_names):
        Tag = Pool().get('brainbow.tag')

        if field_names and not field_names & {'tags'}:
            return

        required_tags = Tag.search([
                ('required', '=', True),
                ('view', '=', True),
                ])
        unique_tags = Tag.search([
                ('unique', '=', True),
                ('view', '=', True),
                ])

        required_children = []
        for required in required_tags:
            children = Tag.search([
                    ('parent', 'child_of', [required]),
                    ('id', '!=', required),
                    ])
            required_children.append(children)

        for document in documents:
            if required_children:
                exists = cls.check_if_exist(required_children, document.tags)
                if not exists:
                    cat_required = [c.name for c in required_tags]
                    raise UserError(gettext('brainbow.missing_tags',
                        document=document.rec_name,
                        tags=', '.join(cat_required[:3])))

            if unique_tags:
                for unique_tag in unique_tags:
                    # Check if we have more than one child tag for each
                    # unique tag
                    children = Tag.search([
                            ('parent', 'child_of', unique_tag),
                            ])
                    if len(set(children) & set(document.tags)) > 1:
                        raise UserError(
                            gettext('brainbow.repeated_unique',
                            document=document.rec_name))

    @staticmethod
    def check_if_exist(list1, list2):
        for party in list2:
            for required_parent in list1:
                if party in required_parent:
                    list1.remove(required_parent)
        return list1 == []


class DocumentReaderGroup(ModelSQL):
    'Brainbow Document - Reader Group'
    __name__ = 'brainbow.document-reader-group'

    document = fields.Many2One('brainbow.document', 'Document', required=True,
        ondelete='CASCADE')
    reader_group = fields.Many2One('res.group', 'Reader Group', required=True,
        ondelete='CASCADE')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls.__access__.add('document')
        cls._sql_constraints += [
            ('document_reader_group_uniq', Unique(t, t.document, t.reader_group),
                'brainbow.msg_document_reader_group_uniq')
        ]


class DocumentWriterGroup(ModelSQL):
    'Brainbow Document - Writer Group'
    __name__ = 'brainbow.document-writer-group'

    document = fields.Many2One('brainbow.document', 'Document', required=True,
        ondelete='CASCADE')
    writer_group = fields.Many2One('res.group', 'Writer Group', required=True,
        ondelete='CASCADE')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.__access__.add('document')
        t = cls.__table__()
        cls._sql_constraints += [
            ('document_writer_group_uniq', Unique(t, t.document, t.writer_group),
                'brainbow.msg_document_writer_group_uniq')
        ]


class DocumentTag(ModelSQL):
    'Brainbow Document - Tag'
    __name__ = 'brainbow.document-tag'

    document = fields.Many2One('brainbow.document', 'Document', required=True,
        ondelete='CASCADE')
    tag = fields.Many2One('brainbow.tag', 'Tag', required=True,
        ondelete='CASCADE')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.__access__.add('document')
        t = cls.__table__()
        cls._sql_constraints += [
            ('document_tag_uniq', Unique(t, t.document, t.tag),
                'brainbow.msg_document_tag_uniq')
        ]
