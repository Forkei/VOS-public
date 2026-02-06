"""
Advanced calculator tools for comprehensive mathematical operations.

Includes basic arithmetic, advanced math functions, statistics, random numbers,
linear algebra, number theory, and unit conversions.
"""

from typing import Dict, Any, List, Optional, Tuple
import math
import random
import statistics
import re
import logging
from collections import Counter
import numexpr as ne
from vos_sdk.tools.base import BaseTool

logger = logging.getLogger(__name__)


class BasicCalculationTool(BaseTool):
    """Performs basic and advanced arithmetic calculations"""

    def __init__(self):
        super().__init__(
            name="basic_calculation",
            description="Performs arithmetic calculations including addition, subtraction, multiplication, division, power, square root, absolute value, rounding, and complex expressions. Supports parentheses and order of operations."
        )
        self.parameters = [
            {
                "name": "expression",
                "type": "string",
                "description": "Mathematical expression to evaluate (e.g., '2 + 2', '10 * (5 + 3)', 'sqrt(16)', '2^3', 'abs(-5)', 'round(3.14159, 2)'). Supports +, -, *, /, ^, sqrt(), abs(), round(), floor(), ceil(), max(), min()",
                "required": True
            }
        ]

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate expression argument"""
        if "expression" not in arguments:
            return False, "Missing required argument: 'expression'"

        expression = arguments.get("expression")

        if not isinstance(expression, str):
            return False, f"'expression' must be a string, got {type(expression).__name__}"

        if not expression.strip():
            return False, "'expression' cannot be empty"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Execute basic calculation using numexpr for safe evaluation"""
        try:
            expression = arguments.get("expression", "").strip()
            original_expression = expression

            # Replace ^ with ** for power operations (numexpr uses **)
            expression = expression.replace('^', '**')

            # Prepare local variables for numexpr (supports sqrt directly)
            # Note: numexpr has built-in sqrt, abs, and other functions
            # For functions not in numexpr, we'll pre-calculate

            # Handle functions that numexpr doesn't support natively
            # Extract and evaluate these separately
            import_funcs = {}

            # Handle round, floor, ceil, max, min by evaluating simple expressions
            # For now, use numexpr's supported functions only
            # numexpr supports: sqrt, abs, sin, cos, tan, arcsin, arccos, arctan, etc.

            try:
                # Evaluate using numexpr - it's safer than eval
                result = ne.evaluate(expression, local_dict={}, global_dict={}).item()
            except (KeyError, AttributeError, SyntaxError):
                # If numexpr can't handle it (e.g., contains round, max, min),
                # fall back to safer eval with restricted namespace
                logger.debug(f"🔍 Expression not supported by numexpr, using restricted eval: {expression}")

                # Replace mathematical functions for eval fallback
                expression = re.sub(r'sqrt\(([^)]+)\)', r'math.sqrt(\1)', expression)
                expression = re.sub(r'abs\(([^)]+)\)', r'abs(\1)', expression)
                expression = re.sub(r'round\(([^)]+)\)', r'round(\1)', expression)
                expression = re.sub(r'floor\(([^)]+)\)', r'math.floor(\1)', expression)
                expression = re.sub(r'ceil\(([^)]+)\)', r'math.ceil(\1)', expression)
                expression = re.sub(r'max\(([^)]+)\)', r'max(\1)', expression)
                expression = re.sub(r'min\(([^)]+)\)', r'min(\1)', expression)

                # Use eval with very restricted namespace as fallback
                result = eval(expression, {"__builtins__": {}}, {
                    "math": math,
                    "abs": abs,
                    "round": round,
                    "max": max,
                    "min": min
                })

            # Round display to 10 decimal places, keep full precision in result
            display_result = round(result, 10) if isinstance(result, float) else result

            logger.info(f"✅ Successfully calculated: {original_expression} = {display_result}")

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "expression": original_expression,
                    "result": result,
                    "formatted_result": f"{original_expression} = {display_result}"
                }
            )

        except ZeroDivisionError:
            logger.error(f"❌ Division by zero in expression: {arguments.get('expression')}")
            self.send_result_notification(
                status="FAILURE",
                error_message="Division by zero error"
            )
        except (ValueError, TypeError) as e:
            logger.error(f"❌ Invalid value in expression: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Invalid value in expression: {str(e)}"
            )
        except SyntaxError as e:
            logger.error(f"❌ Syntax error in expression: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Invalid expression syntax: {str(e)}"
            )
        except Exception as e:
            logger.error(f"❌ Calculation error: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Calculation error: {str(e)}"
            )


class AdvancedMathTool(BaseTool):
    """Performs advanced mathematical operations"""

    def __init__(self):
        super().__init__(
            name="advanced_math",
            description="Performs advanced mathematical functions including trigonometry (sin, cos, tan, asin, acos, atan), logarithms (log, log10, ln), exponentials, hyperbolic functions, and more. Angles are in radians by default."
        )
        self.parameters = [
            {
                "name": "operation",
                "type": "string",
                "description": "Operation to perform: 'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'sinh', 'cosh', 'tanh', 'log' (natural log), 'log10', 'log2', 'exp', 'factorial', 'degrees', 'radians'",
                "required": True
            },
            {
                "name": "value",
                "type": "number",
                "description": "The value to operate on",
                "required": True
            },
            {
                "name": "base",
                "type": "number",
                "description": "For logarithm with custom base (log only)",
                "required": False
            }
        ]

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate operation and value arguments"""
        if "operation" not in arguments:
            return False, "Missing required argument: 'operation'"

        if "value" not in arguments:
            return False, "Missing required argument: 'value'"

        operation = arguments.get("operation", "").lower()
        value = arguments.get("value")

        # Validate operation is a string
        if not isinstance(operation, str):
            return False, f"'operation' must be a string, got {type(operation).__name__}"

        # Validate value is numeric
        if not isinstance(value, (int, float)):
            return False, f"'value' must be a number, got {type(value).__name__}"

        # Validate factorial input (must be non-negative integer, max 5000)
        if operation == "factorial":
            if value < 0:
                return False, "Factorial requires a non-negative number"
            if value != int(value):
                return False, "Factorial requires an integer value"
            if value > 5000:
                return False, "Factorial input too large (maximum 5000). Large factorials can cause overflow."

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Execute advanced math operation"""
        try:
            operation = arguments.get("operation", "").lower()
            value = arguments.get("value")
            base = arguments.get("base")

            # Trigonometric functions
            if operation == "sin":
                result = math.sin(value)
            elif operation == "cos":
                result = math.cos(value)
            elif operation == "tan":
                result = math.tan(value)
            elif operation == "asin":
                result = math.asin(value)
            elif operation == "acos":
                result = math.acos(value)
            elif operation == "atan":
                result = math.atan(value)

            # Hyperbolic functions
            elif operation == "sinh":
                result = math.sinh(value)
            elif operation == "cosh":
                result = math.cosh(value)
            elif operation == "tanh":
                result = math.tanh(value)

            # Logarithms
            elif operation == "log":
                if base:
                    result = math.log(value, base)
                else:
                    result = math.log(value)  # Natural log (ln)
            elif operation == "log10":
                result = math.log10(value)
            elif operation == "log2":
                result = math.log2(value)

            # Exponential
            elif operation == "exp":
                result = math.exp(value)

            # Factorial (validation already done in validate_arguments)
            elif operation == "factorial":
                result = math.factorial(int(value))
                if value > 100:
                    logger.info(f"🔍 Computed large factorial: {int(value)}! (result has {len(str(result))} digits)")

            # Angle conversions
            elif operation == "degrees":
                result = math.degrees(value)
            elif operation == "radians":
                result = math.radians(value)

            else:
                logger.error(f"❌ Unknown operation: {operation}")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Unknown operation '{operation}'. Valid operations: sin, cos, tan, asin, acos, atan, sinh, cosh, tanh, log, log10, log2, exp, factorial, degrees, radians"
                )
                return

            # Round display to 10 decimal places for floats
            display_result = round(result, 10) if isinstance(result, float) else result

            logger.info(f"✅ {operation}({value}) = {display_result}")

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "operation": operation,
                    "value": value,
                    "result": result,
                    "formatted_result": f"{operation}({value}) = {display_result}"
                }
            )

        except ValueError as e:
            logger.error(f"❌ Invalid value for {operation}: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Invalid value for operation '{operation}': {str(e)}. Check if the value is in the valid domain."
            )
        except OverflowError as e:
            logger.error(f"❌ Overflow in {operation}: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Overflow error: Result too large to compute"
            )
        except Exception as e:
            logger.error(f"❌ Advanced math error in {operation}: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Advanced math error: {str(e)}"
            )


class StatisticsTool(BaseTool):
    """Performs statistical calculations on datasets"""

    def __init__(self):
        super().__init__(
            name="statistics",
            description="Calculates statistical measures including mean, median, mode, standard deviation, variance, min, max, range, sum, and count for a dataset."
        )
        self.parameters = [
            {
                "name": "data",
                "type": "array",
                "description": "Array of numbers to analyze",
                "required": True
            },
            {
                "name": "operations",
                "type": "array",
                "description": "List of operations to perform: 'mean', 'median', 'mode', 'stdev', 'variance', 'min', 'max', 'range', 'sum', 'count', 'all'. Default: ['all']",
                "required": False
            }
        ]

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate data array argument"""
        if "data" not in arguments:
            return False, "Missing required argument: 'data'"

        data = arguments.get("data")

        if not isinstance(data, list):
            return False, f"'data' must be an array, got {type(data).__name__}"

        if not data:
            return False, "'data' array cannot be empty"

        # Validate all elements are numeric
        for i, item in enumerate(data):
            if not isinstance(item, (int, float)):
                return False, f"All data values must be numeric. Found {type(item).__name__} at index {i}"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Execute statistical calculations"""
        try:
            data = arguments.get("data", [])
            operations = arguments.get("operations", ["all"])

            # Convert all to float (validation already confirmed they're numeric)
            data = [float(x) for x in data]

            result_data = {"data_count": len(data)}

            # If 'all' is requested, compute everything
            if "all" in operations:
                operations = ['mean', 'median', 'mode', 'stdev', 'variance', 'min', 'max', 'range', 'sum', 'count']

            # Calculate requested statistics
            if 'mean' in operations:
                result_data['mean'] = round(statistics.mean(data), 10)

            if 'median' in operations:
                result_data['median'] = round(statistics.median(data), 10)

            if 'mode' in operations:
                # Use Counter to find all modes (values with highest frequency)
                counts = Counter(data)
                max_count = max(counts.values())

                if max_count == 1:
                    # All values appear once - no mode
                    result_data['mode'] = None
                    result_data['mode_note'] = "No mode (all values unique)"
                else:
                    # Find all values with max count
                    modes = [value for value, count in counts.items() if count == max_count]
                    if len(modes) == 1:
                        result_data['mode'] = modes[0]
                    else:
                        result_data['mode'] = modes
                        result_data['mode_note'] = f"Multiple modes found (each appears {max_count} times)"

            if 'stdev' in operations:
                if len(data) > 1:
                    result_data['stdev'] = round(statistics.stdev(data), 10)
                else:
                    result_data['stdev'] = None
                    result_data['stdev_note'] = "Standard deviation requires at least 2 values"

            if 'variance' in operations:
                if len(data) > 1:
                    result_data['variance'] = round(statistics.variance(data), 10)
                else:
                    result_data['variance'] = None
                    result_data['variance_note'] = "Variance requires at least 2 values"

            if 'min' in operations:
                result_data['min'] = min(data)

            if 'max' in operations:
                result_data['max'] = max(data)

            if 'range' in operations:
                result_data['range'] = round(max(data) - min(data), 10)

            if 'sum' in operations:
                result_data['sum'] = round(sum(data), 10)

            if 'count' in operations:
                result_data['count'] = len(data)

            logger.info(f"✅ Calculated statistics for {len(data)} data points")

            self.send_result_notification(
                status="SUCCESS",
                result=result_data
            )

        except statistics.StatisticsError as e:
            logger.error(f"❌ Statistics error: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Statistics calculation error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"❌ Unexpected error in statistics: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Statistics calculation error: {str(e)}"
            )


class RandomNumberTool(BaseTool):
    """Generates random numbers with various distributions"""

    def __init__(self):
        super().__init__(
            name="random_number",
            description="Generates random numbers with different distributions: uniform (random integers or floats), normal/gaussian distribution, choice from a list, or shuffle a list."
        )
        self.parameters = [
            {
                "name": "type",
                "type": "string",
                "description": "Type of random generation: 'int' (random integer), 'float' (random float), 'normal' (normal distribution), 'choice' (pick from list), 'shuffle' (shuffle list), 'sample' (random sample from list)",
                "required": True
            },
            {
                "name": "min",
                "type": "number",
                "description": "Minimum value for 'int' or 'float' types",
                "required": False
            },
            {
                "name": "max",
                "type": "number",
                "description": "Maximum value for 'int' or 'float' types",
                "required": False
            },
            {
                "name": "mean",
                "type": "number",
                "description": "Mean for 'normal' distribution (default: 0)",
                "required": False
            },
            {
                "name": "std_dev",
                "type": "number",
                "description": "Standard deviation for 'normal' distribution (default: 1)",
                "required": False
            },
            {
                "name": "choices",
                "type": "array",
                "description": "List of items for 'choice', 'shuffle', or 'sample' types",
                "required": False
            },
            {
                "name": "count",
                "type": "number",
                "description": "Number of random values to generate or sample size (default: 1)",
                "required": False
            }
        ]

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate random number generation arguments"""
        if "type" not in arguments:
            return False, "Missing required argument: 'type'"

        rand_type = arguments.get("type", "").lower()

        if not isinstance(rand_type, str):
            return False, f"'type' must be a string, got {type(rand_type).__name__}"

        valid_types = ['int', 'float', 'normal', 'choice', 'shuffle', 'sample']
        if rand_type not in valid_types:
            return False, f"Invalid type '{rand_type}'. Valid types: {', '.join(valid_types)}"

        # Type-specific validation
        if rand_type in ['choice', 'shuffle', 'sample']:
            if "choices" not in arguments or not arguments.get("choices"):
                return False, f"'choices' array is required for type '{rand_type}'"
            if not isinstance(arguments.get("choices"), list):
                return False, "'choices' must be an array"

        if rand_type == 'sample':
            count = arguments.get("count", 1)
            choices = arguments.get("choices", [])
            if count > len(choices):
                return False, f"Sample size ({count}) cannot be larger than population ({len(choices)})"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Execute random number generation"""
        try:
            rand_type = arguments.get("type", "").lower()
            count = int(arguments.get("count", 1))

            if rand_type == "int":
                min_val = arguments.get("min", 0)
                max_val = arguments.get("max", 100)

                if count == 1:
                    result = random.randint(int(min_val), int(max_val))
                else:
                    result = [random.randint(int(min_val), int(max_val)) for _ in range(count)]

            elif rand_type == "float":
                min_val = arguments.get("min", 0.0)
                max_val = arguments.get("max", 1.0)

                if count == 1:
                    result = random.uniform(min_val, max_val)
                else:
                    result = [random.uniform(min_val, max_val) for _ in range(count)]

            elif rand_type == "normal":
                mean = arguments.get("mean", 0)
                std_dev = arguments.get("std_dev", 1)

                if count == 1:
                    result = random.gauss(mean, std_dev)
                else:
                    result = [random.gauss(mean, std_dev) for _ in range(count)]

            elif rand_type == "choice":
                choices = arguments.get("choices", [])
                if count == 1:
                    result = random.choice(choices)
                else:
                    result = [random.choice(choices) for _ in range(count)]

            elif rand_type == "shuffle":
                choices = arguments.get("choices", [])
                shuffled = choices.copy()
                random.shuffle(shuffled)
                result = shuffled

            elif rand_type == "sample":
                choices = arguments.get("choices", [])
                result = random.sample(choices, count)

            else:
                # This shouldn't happen due to validation, but handle it anyway
                logger.error(f"❌ Unknown random type: {rand_type}")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Unknown type '{rand_type}'"
                )
                return

            logger.info(f"✅ Generated random {rand_type}: count={count if isinstance(result, list) else 1}")

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "type": rand_type,
                    "result": result,
                    "count": count if isinstance(result, list) else 1
                }
            )

        except ValueError as e:
            logger.error(f"❌ Invalid value for random generation: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Invalid value: {str(e)}"
            )
        except Exception as e:
            logger.error(f"❌ Random number generation error: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Random number generation error: {str(e)}"
            )


class NumberTheoryTool(BaseTool):
    """Performs number theory operations"""

    def __init__(self):
        super().__init__(
            name="number_theory",
            description="Performs number theory operations including GCD (greatest common divisor), LCM (least common multiple), prime checking, prime factorization, and divisor finding."
        )
        self.parameters = [
            {
                "name": "operation",
                "type": "string",
                "description": "Operation to perform: 'gcd', 'lcm', 'is_prime', 'prime_factors', 'divisors'",
                "required": True
            },
            {
                "name": "numbers",
                "type": "array",
                "description": "Array of integers for gcd, lcm operations. Single number for is_prime, prime_factors, divisors",
                "required": True
            }
        ]

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate number theory operation arguments"""
        if "operation" not in arguments:
            return False, "Missing required argument: 'operation'"

        if "numbers" not in arguments:
            return False, "Missing required argument: 'numbers'"

        operation = arguments.get("operation", "").lower()
        numbers = arguments.get("numbers")

        if not isinstance(operation, str):
            return False, f"'operation' must be a string, got {type(operation).__name__}"

        if not isinstance(numbers, list):
            return False, f"'numbers' must be an array, got {type(numbers).__name__}"

        if not numbers:
            return False, "'numbers' array cannot be empty"

        # Validate all elements are numeric
        for i, num in enumerate(numbers):
            if not isinstance(num, (int, float)):
                return False, f"All values must be integers. Found {type(num).__name__} at index {i}"

        # Validate operation-specific requirements
        valid_ops = ['gcd', 'lcm', 'is_prime', 'prime_factors', 'divisors']
        if operation not in valid_ops:
            return False, f"Unknown operation '{operation}'. Valid operations: {', '.join(valid_ops)}"

        if operation in ['gcd', 'lcm'] and len(numbers) < 2:
            return False, f"{operation.upper()} requires at least 2 numbers"

        # Add resource limit for prime checking
        if operation == 'is_prime':
            num = abs(int(numbers[0]))
            if num > 1_000_000_000:
                return False, "Number too large for prime checking (maximum 1 billion). Large prime checks can be very slow."

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Execute number theory operation"""
        try:
            operation = arguments.get("operation", "").lower()
            numbers = arguments.get("numbers", [])

            # Convert to integers (validation confirmed they're numeric)
            numbers = [int(x) for x in numbers]

            if operation == "gcd":
                result = numbers[0]
                for num in numbers[1:]:
                    result = math.gcd(result, num)

            elif operation == "lcm":
                result = numbers[0]
                for num in numbers[1:]:
                    result = abs(result * num) // math.gcd(result, num)

            elif operation == "is_prime":
                num = numbers[0]

                # Log warning for large prime checks
                if num > 10_000_000:
                    logger.info(f"🔍 Checking primality of large number: {num:,}")

                if num < 2:
                    result = False
                elif num == 2:
                    result = True
                elif num % 2 == 0:
                    result = False
                else:
                    result = all(num % i != 0 for i in range(3, int(math.sqrt(num)) + 1, 2))

            elif operation == "prime_factors":
                num = abs(numbers[0])
                factors = []
                d = 2
                while d * d <= num:
                    while num % d == 0:
                        factors.append(d)
                        num //= d
                    d += 1
                if num > 1:
                    factors.append(num)
                result = factors

            elif operation == "divisors":
                num = abs(numbers[0])
                divisors = []
                for i in range(1, int(math.sqrt(num)) + 1):
                    if num % i == 0:
                        divisors.append(i)
                        if i != num // i:
                            divisors.append(num // i)
                result = sorted(divisors)

            else:
                # Shouldn't happen due to validation
                logger.error(f"❌ Unknown number theory operation: {operation}")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Unknown operation '{operation}'"
                )
                return

            logger.info(f"✅ {operation} completed for {numbers}")

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "operation": operation,
                    "numbers": numbers,
                    "result": result
                }
            )

        except ValueError as e:
            logger.error(f"❌ Invalid value for number theory: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Invalid value: {str(e)}"
            )
        except Exception as e:
            logger.error(f"❌ Number theory error: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Number theory error: {str(e)}"
            )


class LinearAlgebraTool(BaseTool):
    """Performs linear algebra operations on vectors and matrices"""

    def __init__(self):
        super().__init__(
            name="linear_algebra",
            description="Performs vector and matrix operations including dot product, cross product, magnitude, vector addition/subtraction, scalar multiplication, and matrix operations."
        )
        self.parameters = [
            {
                "name": "operation",
                "type": "string",
                "description": "Operation: 'dot_product', 'cross_product', 'magnitude', 'normalize', 'vector_add', 'vector_subtract', 'scalar_multiply', 'transpose', 'determinant'",
                "required": True
            },
            {
                "name": "vector1",
                "type": "array",
                "description": "First vector (array of numbers)",
                "required": False
            },
            {
                "name": "vector2",
                "type": "array",
                "description": "Second vector (array of numbers)",
                "required": False
            },
            {
                "name": "matrix",
                "type": "array",
                "description": "Matrix (2D array of numbers)",
                "required": False
            },
            {
                "name": "scalar",
                "type": "number",
                "description": "Scalar value for scalar multiplication",
                "required": False
            }
        ]

    def _validate_matrix(self, matrix: List) -> Tuple[bool, Optional[str]]:
        """Helper to validate matrix is rectangular (all rows same length)"""
        if not matrix or not isinstance(matrix, list):
            return False, "Matrix must be a non-empty 2D array"

        if not all(isinstance(row, list) for row in matrix):
            return False, "Matrix must be a 2D array (list of lists)"

        # Check all rows have the same length
        row_length = len(matrix[0])
        if not all(len(row) == row_length for row in matrix):
            return False, "Matrix must be rectangular (all rows must have the same length)"

        # Check all elements are numeric
        for i, row in enumerate(matrix):
            for j, val in enumerate(row):
                if not isinstance(val, (int, float)):
                    return False, f"All matrix elements must be numeric. Found {type(val).__name__} at [{i}][{j}]"

        return True, None

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate linear algebra operation arguments"""
        if "operation" not in arguments:
            return False, "Missing required argument: 'operation'"

        operation = arguments.get("operation", "").lower()

        if not isinstance(operation, str):
            return False, f"'operation' must be a string, got {type(operation).__name__}"

        valid_ops = ['dot_product', 'cross_product', 'magnitude', 'normalize', 'vector_add', 'vector_subtract', 'scalar_multiply', 'transpose', 'determinant']
        if operation not in valid_ops:
            return False, f"Unknown operation '{operation}'. Valid operations: {', '.join(valid_ops)}"

        # Operation-specific validation
        vector_ops = ['dot_product', 'cross_product', 'magnitude', 'normalize', 'vector_add', 'vector_subtract', 'scalar_multiply']
        matrix_ops = ['transpose', 'determinant']

        if operation in vector_ops:
            if operation in ['dot_product', 'cross_product', 'vector_add', 'vector_subtract']:
                if "vector1" not in arguments or "vector2" not in arguments:
                    return False, f"'{operation}' requires both vector1 and vector2"
            elif operation == 'scalar_multiply':
                if "vector1" not in arguments or "scalar" not in arguments:
                    return False, "'scalar_multiply' requires vector1 and scalar"
            else:  # magnitude, normalize
                if "vector1" not in arguments:
                    return False, f"'{operation}' requires vector1"

        if operation in matrix_ops:
            if "matrix" not in arguments:
                return False, f"'{operation}' requires matrix"

            # Validate matrix structure
            matrix = arguments.get("matrix")
            is_valid, error_msg = self._validate_matrix(matrix)
            if not is_valid:
                return False, error_msg

            # Check determinant matrix is square and 2x2 or 3x3
            if operation == 'determinant':
                rows = len(matrix)
                cols = len(matrix[0]) if matrix else 0
                if rows != cols:
                    return False, f"Determinant requires a square matrix, got {rows}x{cols}"
                if rows not in [2, 3]:
                    return False, f"Determinant only supported for 2x2 and 3x3 matrices, got {rows}x{rows}"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Execute linear algebra operation"""
        try:
            operation = arguments.get("operation", "").lower()
            vector1 = arguments.get("vector1")
            vector2 = arguments.get("vector2")
            matrix = arguments.get("matrix")
            scalar = arguments.get("scalar")

            if operation == "dot_product":
                if len(vector1) != len(vector2):
                    self.send_result_notification(
                        status="FAILURE",
                        error_message=f"Vectors must have the same length. Got {len(vector1)} and {len(vector2)}"
                    )
                    return
                result = sum(a * b for a, b in zip(vector1, vector2))

            elif operation == "cross_product":
                if len(vector1) != 3 or len(vector2) != 3:
                    self.send_result_notification(
                        status="FAILURE",
                        error_message=f"Cross product requires 3D vectors. Got {len(vector1)}D and {len(vector2)}D"
                    )
                    return
                result = [
                    vector1[1] * vector2[2] - vector1[2] * vector2[1],
                    vector1[2] * vector2[0] - vector1[0] * vector2[2],
                    vector1[0] * vector2[1] - vector1[1] * vector2[0]
                ]

            elif operation == "magnitude":
                result = math.sqrt(sum(x * x for x in vector1))

            elif operation == "normalize":
                mag = math.sqrt(sum(x * x for x in vector1))
                if mag == 0:
                    logger.error("❌ Cannot normalize zero vector")
                    self.send_result_notification(
                        status="FAILURE",
                        error_message="Cannot normalize zero vector. Magnitude is zero."
                    )
                    return
                result = [x / mag for x in vector1]

            elif operation == "vector_add":
                if len(vector1) != len(vector2):
                    self.send_result_notification(
                        status="FAILURE",
                        error_message=f"Vectors must have the same length. Got {len(vector1)} and {len(vector2)}"
                    )
                    return
                result = [a + b for a, b in zip(vector1, vector2)]

            elif operation == "vector_subtract":
                if len(vector1) != len(vector2):
                    self.send_result_notification(
                        status="FAILURE",
                        error_message=f"Vectors must have the same length. Got {len(vector1)} and {len(vector2)}"
                    )
                    return
                result = [a - b for a, b in zip(vector1, vector2)]

            elif operation == "scalar_multiply":
                result = [x * scalar for x in vector1]

            elif operation == "transpose":
                # Validation confirmed matrix is rectangular
                result = [[matrix[j][i] for j in range(len(matrix))] for i in range(len(matrix[0]))]

            elif operation == "determinant":
                # Validation confirmed matrix is square and 2x2 or 3x3
                if len(matrix) == 2:
                    result = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
                else:  # 3x3
                    result = (
                        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1]) -
                        matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0]) +
                        matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
                    )

            else:
                # Shouldn't happen due to validation
                logger.error(f"❌ Unknown linear algebra operation: {operation}")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Unknown operation '{operation}'"
                )
                return

            # Round floats for display if result is numeric
            display_result = result
            if isinstance(result, float):
                display_result = round(result, 10)
            elif isinstance(result, list) and result and isinstance(result[0], float):
                display_result = [round(x, 10) for x in result]

            logger.info(f"✅ {operation} completed")

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "operation": operation,
                    "result": result,
                    "formatted_result": display_result
                }
            )

        except (IndexError, KeyError) as e:
            logger.error(f"❌ Index/Key error in linear algebra: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Invalid vector/matrix structure: {str(e)}"
            )
        except ZeroDivisionError as e:
            logger.error(f"❌ Division by zero in linear algebra: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message="Division by zero error"
            )
        except Exception as e:
            logger.error(f"❌ Linear algebra error: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Linear algebra error: {str(e)}"
            )


class UnitConversionTool(BaseTool):
    """Converts between different units of measurement"""

    def __init__(self):
        super().__init__(
            name="unit_conversion",
            description="Converts between units of length, mass, temperature, area, volume, speed, time, and more."
        )
        self.parameters = [
            {
                "name": "value",
                "type": "number",
                "description": "The value to convert",
                "required": True
            },
            {
                "name": "from_unit",
                "type": "string",
                "description": "Source unit (e.g., 'meters', 'feet', 'celsius', 'kg', etc.)",
                "required": True
            },
            {
                "name": "to_unit",
                "type": "string",
                "description": "Target unit (e.g., 'feet', 'meters', 'fahrenheit', 'lbs', etc.)",
                "required": True
            }
        ]

    # Unit categories for validation
    UNIT_CATEGORIES = {
        'length': ['meters', 'm', 'meter', 'kilometers', 'km', 'kilometer', 'centimeters', 'cm', 'centimeter',
                   'millimeters', 'mm', 'millimeter', 'miles', 'mi', 'mile', 'feet', 'ft', 'foot',
                   'inches', 'in', 'inch', 'yards', 'yd', 'yard'],
        'mass': ['kilograms', 'kg', 'kilogram', 'grams', 'g', 'gram', 'milligrams', 'mg', 'milligram',
                 'pounds', 'lbs', 'lb', 'pound', 'ounces', 'oz', 'ounce', 'tons', 'ton', 'tonnes', 'tonne'],
        'time': ['seconds', 's', 'sec', 'second', 'minutes', 'min', 'minute', 'hours', 'hr', 'hour',
                 'days', 'day', 'weeks', 'week'],
        'speed': ['mps', 'm/s', 'kph', 'km/h', 'kmph', 'mph', 'mi/h'],
        'area': ['square_meters', 'sq_m', 'm2', 'square_kilometers', 'sq_km', 'km2',
                 'square_feet', 'sq_ft', 'ft2', 'acres', 'acre'],
        'volume': ['liters', 'l', 'liter', 'milliliters', 'ml', 'milliliter',
                   'gallons', 'gal', 'gallon', 'quarts', 'qt', 'quart', 'cups', 'cup'],
        'temperature': ['celsius', 'c', 'fahrenheit', 'f', 'kelvin', 'k']
    }

    # Conversion factors to base units
    CONVERSIONS = {
        # Length (base: meters)
        'meters': 1, 'm': 1, 'meter': 1,
        'kilometers': 1000, 'km': 1000, 'kilometer': 1000,
        'centimeters': 0.01, 'cm': 0.01, 'centimeter': 0.01,
        'millimeters': 0.001, 'mm': 0.001, 'millimeter': 0.001,
        'miles': 1609.34, 'mi': 1609.34, 'mile': 1609.34,
        'feet': 0.3048, 'ft': 0.3048, 'foot': 0.3048,
        'inches': 0.0254, 'in': 0.0254, 'inch': 0.0254,
        'yards': 0.9144, 'yd': 0.9144, 'yard': 0.9144,

        # Mass (base: kilograms)
        'kilograms': 1, 'kg': 1, 'kilogram': 1,
        'grams': 0.001, 'g': 0.001, 'gram': 0.001,
        'milligrams': 0.000001, 'mg': 0.000001, 'milligram': 0.000001,
        'pounds': 0.453592, 'lbs': 0.453592, 'lb': 0.453592, 'pound': 0.453592,
        'ounces': 0.0283495, 'oz': 0.0283495, 'ounce': 0.0283495,
        'tons': 1000, 'ton': 1000, 'tonnes': 1000, 'tonne': 1000,

        # Time (base: seconds)
        'seconds': 1, 's': 1, 'sec': 1, 'second': 1,
        'minutes': 60, 'min': 60, 'minute': 60,
        'hours': 3600, 'hr': 3600, 'hour': 3600,
        'days': 86400, 'day': 86400,
        'weeks': 604800, 'week': 604800,

        # Speed (base: m/s)
        'mps': 1, 'm/s': 1,
        'kph': 0.277778, 'km/h': 0.277778, 'kmph': 0.277778,
        'mph': 0.44704, 'mi/h': 0.44704,

        # Area (base: square meters)
        'square_meters': 1, 'sq_m': 1, 'm2': 1,
        'square_kilometers': 1000000, 'sq_km': 1000000, 'km2': 1000000,
        'square_feet': 0.092903, 'sq_ft': 0.092903, 'ft2': 0.092903,
        'acres': 4046.86, 'acre': 4046.86,

        # Volume (base: liters)
        'liters': 1, 'l': 1, 'liter': 1,
        'milliliters': 0.001, 'ml': 0.001, 'milliliter': 0.001,
        'gallons': 3.78541, 'gal': 3.78541, 'gallon': 3.78541,
        'quarts': 0.946353, 'qt': 0.946353, 'quart': 0.946353,
        'cups': 0.236588, 'cup': 0.236588,
    }

    def _get_unit_category(self, unit: str) -> Optional[str]:
        """Helper to get the category of a unit"""
        unit_lower = unit.lower()
        for category, units in self.UNIT_CATEGORIES.items():
            if unit_lower in units:
                return category
        return None

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate unit conversion arguments"""
        if "value" not in arguments:
            return False, "Missing required argument: 'value'"

        if "from_unit" not in arguments:
            return False, "Missing required argument: 'from_unit'"

        if "to_unit" not in arguments:
            return False, "Missing required argument: 'to_unit'"

        value = arguments.get("value")
        from_unit = arguments.get("from_unit")
        to_unit = arguments.get("to_unit")

        # Validate types
        if not isinstance(value, (int, float)):
            return False, f"'value' must be a number, got {type(value).__name__}"

        if not isinstance(from_unit, str):
            return False, f"'from_unit' must be a string, got {type(from_unit).__name__}"

        if not isinstance(to_unit, str):
            return False, f"'to_unit' must be a string, got {type(to_unit).__name__}"

        from_unit_lower = from_unit.lower()
        to_unit_lower = to_unit.lower()

        # Check units exist
        from_category = self._get_unit_category(from_unit_lower)
        to_category = self._get_unit_category(to_unit_lower)

        if not from_category:
            return False, f"Unknown unit '{from_unit}'. Please check supported units."

        if not to_category:
            return False, f"Unknown unit '{to_unit}'. Please check supported units."

        # Check units are in same category
        if from_category != to_category:
            return False, f"Cannot convert {from_category} ({from_unit}) to {to_category} ({to_unit}). Units must be in the same category."

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Execute unit conversion"""
        try:
            value = arguments.get("value")
            from_unit = arguments.get("from_unit", "").lower()
            to_unit = arguments.get("to_unit", "").lower()

            # Special handling for temperature
            if from_unit in ['celsius', 'c', 'fahrenheit', 'f', 'kelvin', 'k']:
                result = self._convert_temperature(value, from_unit, to_unit)
            else:
                # Standard conversion using base units (validation confirmed units exist)
                # Convert to base unit, then to target unit
                base_value = value * self.CONVERSIONS[from_unit]
                result = base_value / self.CONVERSIONS[to_unit]

            # Round to 10 decimal places for display
            display_result = round(result, 10)

            logger.info(f"✅ Converted {value} {from_unit} to {display_result} {to_unit}")

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "value": value,
                    "from_unit": from_unit,
                    "to_unit": to_unit,
                    "result": result,
                    "formatted_result": f"{value} {from_unit} = {display_result} {to_unit}"
                }
            )

        except ValueError as e:
            logger.error(f"❌ Value error in unit conversion: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=str(e)
            )
        except ZeroDivisionError as e:
            logger.error(f"❌ Division by zero in unit conversion: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message="Division by zero error in conversion"
            )
        except Exception as e:
            logger.error(f"❌ Unit conversion error: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Unit conversion error: {str(e)}"
            )

    def _convert_temperature(self, value: float, from_unit: str, to_unit: str) -> float:
        """Convert temperature between Celsius, Fahrenheit, and Kelvin"""
        # Normalize unit names
        from_unit = from_unit.lower()
        to_unit = to_unit.lower()

        # Convert to Celsius first
        if from_unit in ['celsius', 'c']:
            celsius = value
        elif from_unit in ['fahrenheit', 'f']:
            celsius = (value - 32) * 5/9
        elif from_unit in ['kelvin', 'k']:
            celsius = value - 273.15
        else:
            raise ValueError(f"Unknown temperature unit: {from_unit}")

        # Validate temperature is not below absolute zero
        if celsius < -273.15:
            raise ValueError(f"Temperature below absolute zero (-273.15°C / -459.67°F / 0K). Got {celsius:.2f}°C")

        # Convert from Celsius to target
        if to_unit in ['celsius', 'c']:
            return celsius
        elif to_unit in ['fahrenheit', 'f']:
            return celsius * 9/5 + 32
        elif to_unit in ['kelvin', 'k']:
            return celsius + 273.15
        else:
            raise ValueError(f"Unknown temperature unit: {to_unit}")
