def plural_ru(num, form_1, form_234, form_many):
    rem_10 = num % 10
    rem_100 = num % 100

    if 10 < rem_100 < 20:
        return form_many
    if rem_10 == 1:
        return form_1
    if 2 <= rem_10 <= 4:
        return form_234

    return form_many
