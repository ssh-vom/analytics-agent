
print('hello from sandbox')
import pathlib
p = pathlib.Path('artifacts')
p.mkdir(exist_ok=True)
(p / 'note.md').write_text('# ok')
