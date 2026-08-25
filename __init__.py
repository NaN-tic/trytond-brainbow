from trytond.pool import Pool
from . import document

def register():
    Pool.register(
        document.Index,
        document.Tag,
        document.Document,
        document.DocumentReaderGroup,
        document.DocumentWriterGroup,
        document.DocumentTag,
        module='brainbow', type_='model')
