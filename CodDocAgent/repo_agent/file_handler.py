# FileHandler 类，实现对文件的读写操作，这里的文件包括markdown文件和python文件
# repo_agent/file_handler.py
import ast
import json
import os
from pathlib import Path

import javalang
importr re
import git
from colorama import Fore, Style
from tqdm import tqdm

from repo_agent.log import logger
from repo_agent.settings import SettingsManager
from repo_agent.utils.gitignore_checker import GitignoreChecker
from repo_agent.utils.meta_info_utils import latest_verison_substring


class FileHandler:
    """
    历变更后的文件的循环中，为每个变更后文件（也就是当前文件）创建一个实例
    """

    def __init__(self, repo_path, file_path):
        self.file_path = file_path  # 这里的file_path是相对于仓库根目录的路径
        self.repo_path = repo_path

        setting = SettingsManager.get_setting()

        self.project_hierarchy = (
            setting.project.target_repo / setting.project.hierarchy_name
        )

    def read_file(self):
        """
        Read the file content

        Returns:
            str: The content of the current changed file
        """
        abs_file_path = os.path.join(self.repo_path, self.file_path)

        with open(abs_file_path, "r", encoding="utf-8") as file:
            content = file.read()
        return content

    def get_obj_code_info(
        self, code_type, code_name, start_line, end_line, params, file_path=None
    ):
        """
        Get the code information for a given object.

        Args:
            code_type (str): The type of the code.
            code_name (str): The name of the code.
            start_line (int): The starting line number of the code.
            end_line (int): The ending line number of the code.
            parent (str): The parent of the code.
            file_path (str, optional): The file path. Defaults to None.

        Returns:
            dict: A dictionary containing the code information.
        """

        code_info = {}
        code_info["type"] = code_type
        code_info["name"] = code_name
        code_info["md_content"] = []
        code_info["code_start_line"] = start_line
        code_info["code_end_line"] = end_line
        code_info["params"] = params

        with open(
            os.path.join(
                self.repo_path, file_path if file_path != None else self.file_path
            ),
            "r",
            encoding="utf-8",
        ) as code_file:
            lines = code_file.readlines()
            code_content = "".join(lines[start_line - 1 : end_line])
            # 获取对象名称在第一行代码中的位置
            name_column = lines[start_line - 1].find(code_name)
            # 判断代码中是否有return字样
            if "return" in code_content:
                have_return = True
            else:
                have_return = False

            code_info["have_return"] = have_return
            # # 使用 json.dumps 来转义字符串，并去掉首尾的引号
            # code_info['code_content'] = json.dumps(code_content)[1:-1]
            code_info["code_content"] = code_content
            code_info["name_column"] = name_column

        return code_info

    def write_file(self, file_path, content):
        """
        Write content to a file.

        Args:
            file_path (str): The relative path of the file.
            content (str): The content to be written to the file.
        """
        # 确保file_path是相对路径
        if file_path.startswith("/"):
            # 移除开头的 '/'
            file_path = file_path[1:]

        abs_file_path = os.path.join(self.repo_path, file_path)
        os.makedirs(os.path.dirname(abs_file_path), exist_ok=True)
        with open(abs_file_path, "w", encoding="utf-8") as file:
            file.write(content)

    def get_modified_file_versions(self):
        """
        Get the current and previous versions of the modified file.

        Returns:
            tuple: A tuple containing the current version and the previous version of the file.
        """
        repo = git.Repo(self.repo_path)

        # Read the file in the current working directory (current version)
        current_version_path = os.path.join(self.repo_path, self.file_path)
        with open(current_version_path, "r", encoding="utf-8") as file:
            current_version = file.read()

        # Get the file version from the last commit (previous version)
        commits = list(repo.iter_commits(paths=self.file_path, max_count=1))
        previous_version = None
        if commits:
            commit = commits[0]
            try:
                previous_version = (
                    (commit.tree / self.file_path).data_stream.read().decode("utf-8")
                )
            except KeyError:
                previous_version = None  # The file may be newly added and not present in previous commits

        return current_version, previous_version

    def get_end_lineno(self, node):
        """
        Get the end line number of a given node.

        Args:
            node: The node for which to find the end line number.

        Returns:
            int: The end line number of the node. Returns -1 if the node does not have a line number.
        """
        if not hasattr(node, "lineno"):
            return -1  # 返回-1表示此节点没有行号

        end_lineno = node.lineno
        for child in ast.iter_child_nodes(node):
            child_end = getattr(child, "end_lineno", None) or self.get_end_lineno(child)
            if child_end > -1:  # 只更新当子节点有有效行号时
                end_lineno = max(end_lineno, child_end)
        return end_lineno

    def add_parent_references(self, node, parent=None):
        """
        Adds a parent reference to each node in the AST.

        Args:
            node: The current node in the AST.

        Returns:
            None
        """
        for child in ast.iter_child_nodes(node):
            child.parent = node
            self.add_parent_references(child, node)

    def get_functions_and_classes(self, code_content):
        """
        Retrieves all functions, classes, their parameters (if any), and their hierarchical relationships
        from Python, Java, and Kotlin source files.

        Returns:
            List of tuples (type, name, start_line, end_line, parent_name, parameters).
        """
        suffix = Path(self.file_path).suffix.lower()

        # Java: parse classes and methods via javalang
        if suffix == ".java":
            tree = javalang.parse.parse(code_content)
            items = []
            for _, class_node in tree.filter(javalang.tree.ClassDeclaration):
                cls_name = class_node.name
                start = class_node.position.line if class_node.position else 0
                items.append(("ClassDef", cls_name, start, None, None, []))
                for method in class_node.methods:
                    m_name = method.name
                    m_start = method.position.line if method.position else start
                    params = [p.name for p in method.parameters]
                    items.append(("FunctionDef", f"{cls_name}.{m_name}", m_start, None, cls_name, params))
            return items

        # Kotlin: simple regex-based parser for classes and functions
        if suffix == ".kt":
            items = []
            lines = code_content.splitlines()
            parent_stack = []
            for idx, line in enumerate(lines, start=1):
                # class declaration
                m_cls = re.match(r"\s*(?:public\s+)?class\s+(\w+)", line)
                if m_cls:
                    cls_name = m_cls.group(1)
                    parent_stack = [cls_name]
                    items.append(("ClassDef", cls_name, idx, None, None, []))
                    continue
                # function declaration
                m_fun = re.match(r"\s*fun\s+(\w+)\s*\(([^)]*)\)", line)
                if m_fun:
                    fn_name = m_fun.group(1)
                    params = [p.strip().split(':')[0] for p in m_fun.group(2).split(',') if p.strip()]
                    parent = parent_stack[-1] if parent_stack else None
                    items.append(("FunctionDef", fn_name, idx, None, parent, params))
            return items

        # Python: parse via AST
        if suffix == ".py":
            tree = ast.parse(code_content)
            self.add_parent_references(tree)
            items = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    kind = type(node).__name__
                    name = node.name
                    start = node.lineno
                    end = getattr(node, 'end_lineno', None) or self.get_end_lineno(node)
                    # find parent name if nested
                    parent = None
                    curr = getattr(node, 'parent', None)
                    while curr:
                        if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            parent = curr.name
                            break
                        curr = getattr(curr, 'parent', None)
                    params = []
                    if hasattr(node, 'args'):
                        params = [arg.arg for arg in node.args.args]
                    items.append((kind, name, start, end, parent, params))
            return items

        # default: unsupported file type
        return []

    def add_parent_references(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
            self.add_parent_references(child)

    def generate_file_structure(self, file_path):
        """
        Generates the file structure for the given file path.

        Args:
            file_path (str): The relative path of the file.

        Returns:
            dict: A dictionary containing the file path and the generated file structure.

        Output example:
        {
            "function_name": {
                "type": "function",
                "start_line": 10,
                ··· ···
                "end_line": 20,
                "parent": "class_name"
            },
            "class_name": {
                "type": "class",
                "start_line": 5,
                ··· ···
                "end_line": 25,
                "parent": None
            }
        }
        """
        with open(os.path.join(self.repo_path, file_path), "r", encoding="utf-8") as f:
            content = f.read()
            structures = self.get_functions_and_classes(content)
            file_objects = []  # 以列表的形式存储
            for struct in structures:
                structure_type, name, start_line, end_line, params = struct
                code_info = self.get_obj_code_info(
                    structure_type, name, start_line, end_line, params, file_path
                )
                file_objects.append(code_info)

        return file_objects

    def generate_overall_structure(self, file_path_reflections, jump_files) -> dict:
        """获取目标仓库的文件情况，通过AST-walk获取所有对象等情况。
        对于jump_files: 不会parse，当做不存在
        """
        repo_structure = {}
        gitignore_checker = GitignoreChecker(
            directory=self.repo_path,
            gitignore_path=os.path.join(self.repo_path, ".gitignore"),
        )

        bar = tqdm(gitignore_checker.check_files_and_folders())
        for not_ignored_files in bar:
            normal_file_names = not_ignored_files
            if not_ignored_files in jump_files:
                print(
                    f"{Fore.LIGHTYELLOW_EX}[File-Handler] Unstaged AddFile, ignore this file: {Style.RESET_ALL}{normal_file_names}"
                )
                continue
            elif not_ignored_files.endswith(latest_verison_substring):
                print(
                    f"{Fore.LIGHTYELLOW_EX}[File-Handler] Skip Latest Version, Using Git-Status Version]: {Style.RESET_ALL}{normal_file_names}"
                )
                continue
            # elif not_ignored_files.endswith(latest_version):
            #     """如果某文件被删除但没有暂存，文件系统有fake_file但没有对应的原始文件"""
            #     for k,v in file_path_reflections.items():
            #         if v == not_ignored_files and not os.path.exists(os.path.join(setting.project.target_repo, not_ignored_files)):
            #             print(f"{Fore.LIGHTYELLOW_EX}[Unstaged DeleteFile] load fake-file-content: {Style.RESET_ALL}{k}")
            #             normal_file_names = k #原来的名字
            #             break
            #     if normal_file_names == not_ignored_files:
            #         continue

            # if not_ignored_files in file_path_reflections.keys():
            #     not_ignored_files = file_path_reflections[not_ignored_files] #获取fake_file_path
            #     print(f"{Fore.LIGHTYELLOW_EX}[Unstaged ChangeFile] load fake-file-content: {Style.RESET_ALL}{normal_file_names}")

            try:
                repo_structure[normal_file_names] = self.generate_file_structure(
                    not_ignored_files
                )
            except Exception as e:
                logger.error(
                    f"Alert: An error occurred while generating file structure for {not_ignored_files}: {e}"
                )
                continue
            bar.set_description(f"generating repo structure: {not_ignored_files}")
        return repo_structure

    def convert_to_markdown_file(self, file_path=None):
        """
        Converts the content of a file to markdown format.

        Args:
            file_path (str, optional): The relative path of the file to be converted. If not provided, the default file path, which is None, will be used.

        Returns:
            str: The content of the file in markdown format.

        Raises:
            ValueError: If no file object is found for the specified file path in project_hierarchy.json.
        """
        with open(self.project_hierarchy, "r", encoding="utf-8") as f:
            json_data = json.load(f)

        if file_path is None:
            fi