"""
Deterministic code complexity analyzer utilizing lizard for multi-language AST metrics.
"""

from typing import List, Tuple
import lizard
from pathlib import Path
from ..core.models import FunctionComplexity


class ComplexityAnalyzer:
    """Calculates Cyclomatic Complexity, NLOC, and nesting depth across languages."""

    @classmethod
    def analyze_paths(cls, file_paths: List[Path]) -> Tuple[float, int, List[FunctionComplexity]]:
        """
        Runs lizard analysis on a list of file paths.
        Returns:
            - average_cyclomatic_complexity: float
            - max_cyclomatic_complexity: int
            - top_complex_functions: List[FunctionComplexity]
        """
        if not file_paths:
            return 0.0, 0, []

        str_paths = [str(p) for p in file_paths if p.is_file()]
        if not str_paths:
            return 0.0, 0, []

        file_infos = lizard.analyze(str_paths)

        all_functions: List[FunctionComplexity] = []
        total_cc = 0
        total_funcs = 0
        max_cc = 0

        for file_info in file_infos:
            for func in file_info.function_list:
                total_funcs += 1
                total_cc += func.cyclomatic_complexity
                if func.cyclomatic_complexity > max_cc:
                    max_cc = func.cyclomatic_complexity

                all_functions.append(
                    FunctionComplexity(
                        name=func.name,
                        filename=Path(func.filename).name,
                        nloc=func.nloc,
                        cyclomatic_complexity=func.cyclomatic_complexity,
                    )
                )

        avg_cc = round(total_cc / total_funcs, 2) if total_funcs > 0 else 1.0

        # Sort descending by cyclomatic complexity and pick top 10 worst offenders
        all_functions.sort(key=lambda f: f.cyclomatic_complexity, reverse=True)
        top_offenders = all_functions[:10]

        return avg_cc, max_cc, top_offenders
