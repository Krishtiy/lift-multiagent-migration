# -*- coding: utf-8 -*-
"""Math and statistical functions containing Python 2 legacy idioms."""


def integer_average(numbers):
    """Calculate average using Python 2 integer division.
    
    Gotcha G-05: In Python 2, 7 / 2 returns 3. In Python 3, 7 / 2 returns 3.5.
    If behavioral preservation is required, migration must use // or int(sum / len).
    """
    if not numbers:
        return 0
    return sum(numbers) / len(numbers)


def generate_series(start, count, step=1):
    """Generate an arithmetic progression using xrange.
    
    Gotcha G-03: xrange() does not exist in Python 3 (replaced by range()).
    """
    result = []
    for i in xrange(count):
        result.append(start + (i * step))
    return result


def parse_large_id(id_string):
    """Cast large numeric identifier to long type.
    
    Gotcha G-08: 'long' type and literals in Python 2.
    """
    val = long(id_string)
    # Return with explicit check
    return val


def sort_records_mixed(records):
    """Sort heterogeneous records using Python 2 mixed-type ordering.
    
    Gotcha G-11: Python 2 allowed sorting lists with mixed types (None < ints < strings).
    Python 3 raises TypeError: '<' not supported between instances of 'NoneType' and 'int'.
    """
    # Defensive copy
    copied = list(records)
    # Python 2 sorts mixed types without error
    copied.sort()
    return copied


def custom_compare_sort(numbers):
    """Sort numbers using old cmp parameter.
    
    Gotcha G-11: cmp argument in list.sort() removed in Python 3 in favor of key=.
    """
    def reverse_cmp(a, b):
        if a < b:
            return 1
        elif a > b:
            return -1
        return 0

    nums = list(numbers)
    nums.sort(cmp=reverse_cmp)
    return nums
