from typing import Optional, Union

def calculate_percentage(part: Union[int, float], whole: Union[int, float], decimal_places: Optional[int] = None) -> Union[float, int, None]:
    """
    Calculate the percentage of a part relative to a whole.
    
    Args:
        part (Union[int, float]): The numerator or part value.
        whole (Union[int, float]): The denominator or total value.
        decimal_places (Optional[int]): Number of decimal places to round the result to.
        
    Returns:
        Union[float, int, None]: The calculated percentage, or None if whole is zero.
    """
    if whole == 0:
        return None
    
    result = (part / whole) * 100
    if decimal_places is not None:
        return round(result, decimal_places)
    return result
