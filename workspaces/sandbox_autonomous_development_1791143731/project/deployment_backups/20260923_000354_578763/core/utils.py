from datetime import datetime, timezone

def format_timestamp(timestamp=None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Formats a given timestamp into a string.
    
    :param timestamp: Can be a datetime object, a numeric timestamp (int/float), a string, or None (current time).
    :param fmt: The strftime format string.
    :return: Formatted date-time string.
    """
    if timestamp is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(timestamp, (int, float)):
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    elif isinstance(timestamp, datetime):
        dt = timestamp
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    elif isinstance(timestamp, str):
        try:
            dt = datetime.fromisoformat(timestamp)
        except ValueError:
            dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        try:
            dt = datetime.fromisoformat(str(timestamp))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception as e:
            raise ValueError(f"Invalid timestamp input: {timestamp}") from e
            
    return dt.strftime(fmt)
