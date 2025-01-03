import ast
from importlib.metadata import distributions

def get_imports(file_path):
    with open(file_path, "r") as file:
        tree = ast.parse(file.read())

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module)

    return imports

def update_requirements(file_path, output_file):
    imports = get_imports(file_path)
    installed_packages = {dist.metadata['Name'].lower(): dist.version for dist in distributions()}

    with open(output_file, "w") as req_file:
        for module in imports:
            if module in installed_packages:
                req_file.write(f"{module}=={installed_packages[module]}\n")
            else:
                req_file.write(f"{module}\n")

    print(f"Updated {output_file} with required modules from {file_path}")

if __name__ == "__main__":
    update_requirements("music.py", "requirements.txt")
