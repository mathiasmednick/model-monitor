"""Project-folder selection and a small local recent-project list."""
from pathlib import Path

def remember(path,recent=()):
    project=Path(path).expanduser().resolve()
    if not project.is_dir():raise ValueError('Choose an existing project folder.')
    result=[str(project)];seen={str(project).casefold()}
    for item in recent:
        folder=Path(item).expanduser().resolve();key=str(folder).casefold()
        if key not in seen and folder.is_dir():result.append(str(folder));seen.add(key)
        if len(result)==8:break
    return str(project),result
