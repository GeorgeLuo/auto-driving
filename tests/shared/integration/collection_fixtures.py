"""Small external collection with shared imports and two plugin steps."""

def collection_request(value=7):
    return dict(operation='upload_collection', name='experiment', plugins=[
        dict(name='memory/remember', entrypoint='pkg.plugin:Remember', config={'offset': 2}),
        dict(name='proposal/propose', entrypoint='pkg.plugin:Propose'),
    ], sources={
        'pkg/__init__.py': '', 'pkg/shared.py': f'VALUE = {value}\n',
        'pkg/plugin.py': 'from .shared import VALUE\n'
                         'class Remember:\n'
                         "    plugin_id = 'remember'\n"
                         '    def __init__(self, offset=0): self.value = VALUE + offset\n'
                         'class Propose:\n'
                         "    plugin_id = 'propose'\n"
                         '    def __init__(self): self.value = VALUE\n',
    })
