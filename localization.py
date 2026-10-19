"""English labels for the original built-in data; user-entered names are preserved."""
LABELS = {
    'Информатика': 'Computer Science', 'Математика': 'Mathematics',
    'Физика': 'Physics', 'Химия': 'Chemistry', 'Биология': 'Biology',
    'Экономика': 'Economics', 'Педагогика': 'Education',
    '1-топ': 'Group 1', 'Демо курс': 'Demo course',
    'Ер': 'Male', 'Әйел': 'Female', '18-19 жас': 'Under 20',
    '20-22 жас': '20–22 years', '23-25 жас': '23–25 years', '26+ жас': '26+ years',
}


def english_label(value):
    return LABELS.get(value, value)
