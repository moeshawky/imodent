"""Import analyzer - detects unused, duplicate, and problematic imports."""

import ast
from pathlib import Path
from typing import Optional
from collections import defaultdict

from .base import Analyzer, AnalyzerCapability
from ..analysis.context import AnalysisContext, FileInfo
from ..analysis.findings import Finding, Severity, Location
from ..graph.imports import extract_imports, ImportInfo


class ImportAnalyzer(Analyzer):
    
    @property
    def name(self) -> str:
        return "imports"
    
    @property
    def capabilities(self):
        return {AnalyzerCapability.IMPORTS}
    
    @property
    def languages(self):
        return {"python"}
    
    @property
    def requires_ast(self) -> bool:
        return True
    
    def analyze(self, context: AnalysisContext) -> list[Finding]:
        """Analyze imports for issues."""
        findings = []
        
        for path, file_info in context.files.items():
            if file_info.language != 'python':
                continue
            
            # Extract imports from file
            imports = extract_imports(file_info.content, path)
            
            # Find duplicates
            duplicates = self._find_duplicates(imports)
            findings.extend(duplicates)
            
            # Find unused (check against usage in file)
            unused = self._find_unused_in_file(file_info, imports)
            findings.extend(unused)
            
            # Find potentially unused across project
            # (will be checked against dependency graph)
            for imp in imports:
                # Skip local imports for now
                if self._is_local_import(imp, context):
                    continue
                
                # Check if it's used anywhere in the project
                used_elsewhere = self._check_project_usage(imp, context)
                if not used_elsewhere and imp.name:
                    # Import is unused - create finding with options
                    finding = Finding.create(
                        type="unused_import",
                        severity=Severity.INFO,
                        file=path,
                        message=f"Import '{imp.import_statement}' may be unused",
                        location=Location(line=imp.line),
                        fixable=True,
                        auto_fix_safe=False,  # Needs review
                        import_name=imp.name,
                        import_module=imp.module,
                        usage_count=0,
                        data={'import_info': {
                            'module': imp.module,
                            'name': imp.name,
                            'alias': imp.alias,
                            'is_from': imp.is_from_import
                        }}
                    )
                    findings.append(finding)
        
        return findings
    
    def _find_duplicates(self, imports: list[ImportInfo]) -> list[Finding]:
        """Find duplicate imports in the same file."""
        findings = []
        seen = {}
        
        for imp in imports:
            key = (imp.module, imp.name, imp.alias)
            if key in seen:
                # Duplicate found
                first_line = seen[key].line
                findings.append(Finding.create(
                    type="duplicate_import",
                    severity=Severity.WARNING,
                    file=imp.file,
                    message=f"Duplicate import '{imp.import_statement}' (first at line {first_line})",
                    location=Location(line=imp.line),
                    fixable=True,
                    auto_fix_safe=True,
                    import_name=imp.name,
                    import_module=imp.module,
                    data={'first_occurrence': first_line}
                ))
            else:
                seen[key] = imp
        
        return findings
    
    def _find_unused_in_file(self, file_info: FileInfo, imports: list[ImportInfo]) -> list[Finding]:
        """Find imports that are never used in the file."""
        findings = []
        
        if not file_info.ast_tree:
            return findings
        
        # Collect all names used in the file
        used_names = set()
        for node in ast.walk(file_info.ast_tree):
            if isinstance(node, ast.Name):
                used_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                # For foo.bar, we need foo
                if isinstance(node.value, ast.Name):
                    used_names.add(node.value.id)
            elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                # Decorators
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name):
                        used_names.add(dec.id)
            elif isinstance(node, ast.ClassDef):
                # Base classes and decorators
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        used_names.add(base.id)
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name):
                        used_names.add(dec.id)
        
        # Check each import
        for imp in imports:
            name_to_check = imp.alias or imp.name or imp.module.split('.')[0]
            
            # Special cases - these are often used indirectly
            if name_to_check in ('typing', 'abc', 'dataclasses'):
                continue
            
            if name_to_check not in used_names:
                findings.append(Finding.create(
                    type="unused_import_file",
                    severity=Severity.INFO,
                    file=file_info.path,
                    message=f"Import '{imp.import_statement}' is not used in this file",
                    location=Location(line=imp.line),
                    fixable=True,
                    auto_fix_safe=False,  # Could be used for typing
                    import_name=imp.name,
                    import_module=imp.module,
                    data={'import_info': {
                        'module': imp.module,
                        'name': imp.name,
                        'checked_name': name_to_check
                    }}
                ))
        
        return findings
    
    def _is_local_import(self, imp: ImportInfo, context: AnalysisContext) -> bool:
        """Check if import is from the same project."""
        module = imp.module.split('.')[0]
        return module in context.graph.module_to_file
    
    def _check_project_usage(self, imp: ImportInfo, context: AnalysisContext) -> bool:
        """Check if imported symbol is used elsewhere in project."""
        # This is a simplified check - full implementation would trace symbols
        name = imp.alias or imp.name
        if not name:
            return False
        
        # Check if it's imported anywhere else
        module = imp.module
        return len(context.graph.get_importers(module)) > 1
