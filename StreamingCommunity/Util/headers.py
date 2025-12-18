# 4.04.24

# External library
import ua_generator


# Variable
ua =  ua_generator.generate(device='desktop', browser=('chrome', 'edge'))


def get_userAgent() -> str:
    user_agent =  ua_generator.generate().text
    return user_agent


def get_headers() -> dict:
    return ua.headers.get()