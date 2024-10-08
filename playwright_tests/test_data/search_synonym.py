class SearchSynonyms:

    synonym_dict = {
        # English dialects
        'favorite': ['favourite'],
        'favourite': ['favorite'],

        # Media
        'multimedia': ['media', 'audio', 'sound', 'voice', 'music', 'mp3', 'song', 'picture',
                       'photo', 'image', 'graphic', 'video', 'movie', 'film'],
        'media': ['multimedia', 'audio', 'sound', 'voice', 'music', 'mp3', 'song', 'picture',
                  'photo', 'image', 'graphic', 'video', 'movie', 'film'],
        'audio': ['media', 'sound', 'voice', 'music', 'mp3', 'song'],
        'sound': ['media', 'audio', 'voice', 'music', 'mp3', 'song'],
        'voice': ['media', 'audio', 'sound'],
        'music': ['media', 'audio', 'sound', 'mp3', 'song'],
        'mp3': ['media', 'audio', 'sound', 'music', 'song'],
        'song': ['media', 'audio', 'sound', 'music', 'mp3'],
        'picture': ['media', 'photo', 'image', 'graphic'],
        'photo': ['media', 'picture', 'image', 'graphic'],
        'image': ['media', 'picture', 'photo', 'graphic'],
        'graphic': ['media', 'picture', 'photo', 'image'],
        'video': ['media', 'movie', 'film'],
        'movie': ['media', 'video', 'film'],
        'film': ['media', 'video', 'movie'],

        # Technical terms
        'site': ['website', 'web site', 'page', 'web page', 'webpage'],
        'website': ['site', 'web site', 'page', 'web page', 'webpage'],
        'web site': ['site', 'website', 'page', 'web page', 'webpage'],
        'page': ['site', 'website', 'web site', 'web page', 'webpage'],
        'web page': ['site', 'website', 'web site', 'page', 'webpage'],
        'webpage': ['site', 'website', 'web site', 'page', 'web page'],
        'url': ['uri', 'link', 'hyperlink', 'web address', 'address'],
        'uri': ['url', 'link', 'hyperlink', 'web address', 'address'],
        'link': ['url', 'uri', 'hyperlink', 'web address', 'address'],
        'hyperlink': ['url', 'uri', 'link', 'web address', 'address'],
        'web address': ['url', 'uri', 'link', 'hyperlink', 'address'],
        'address': ['url', 'uri', 'link', 'hyperlink', 'web address'],
        'cache': ['cash', 'cookies'],
        'cash': ['cache', 'cookies'],
        'cookies': ['cache', 'cash'],
        'popup': ['pop up'],
        'pop up': ['popup'],
        'popups': ['pop-up', 'pop-ups', 'pop ups'],
        'virus': ['malware'],
        'malware': ['virus'],
        'mouse': ['cursor'],
        'cursor': ['mouse'],
        'vpn': ['virtual private network'],
        'virtual private network': ['vpn'],
        'mobile': ['phone', 'smartphone', 'smart phone'],
        'phone': ['mobile', 'smartphone', 'smart phone'],
        'smartphone': ['mobile', 'phone', 'smart phone'],
        'smart phone': ['mobile', 'phone', 'smartphone'],

        # Actions
        'delete': ['clear', 'remove'],
        'clear': ['delete', 'remove', 'deleting'],
        'remove': ['delete', 'clear'],
        'start': ['open', 'run'],
        'open': ['start', 'run'],
        'run': ['start', 'open'],
        'change': ['set'],
        'set': ['change'],
        'reset': ['refresh'],
        'refresh': ['reset'],
        'disable': ['block', 'turn off', 'deactivate', 'block'],
        'block': ['disable', 'turn off', 'deactivate'],
        'turn off': ['disable', 'block', 'deactivate'],
        'deactivate': ['disable', 'block', 'turn off'],
        'enable': ['activate', 'allow', 'turn on'],
        'activate': ['enable', 'allow', 'turn on'],
        'allow': ['enable', 'activate', 'turn on'],
        'turn on': ['enable', 'activate', 'allow'],
        'update': ['upgrade'],
        'upgrade': ['update'],
        'signin': ['signup', 'sign up', 'login'],
        'signup': ['signin', 'sign up', 'login'],
        'sign up': ['signin', 'signup', 'login'],
        'login': ['signin', 'signup', 'sign up'],

        # Brands
        'browser': ['firefox'],
        'firefox': ['browser'],
        'social': ['facebook', 'face book', 'twitter', 'myspace', 'reddit', 'instagram'],
        'facebook': ['social', 'face book'],
        'face book': ['social', 'facebook'],
        'twitter': ['social'],
        'myspace': ['social'],
        'reddit': ['social'],
        'instagram': ['social'],
        'modzilla': ['mozilla'],
        'mozzila': ['mozilla'],
        'mozzilla': ['mozilla'],
        'mozila': ['mozilla'],
        'ios': ['ipad', 'iphone', 'ipod'],
        'ipad': ['ios', 'iphone', 'ipod'],
        'iphone': ['ios', 'ipad', 'ipod'],
        'ipod': ['ios', 'ipad', 'iphone'],


        # Product features
        'addon': ['extension', 'theme'],
        'add-on': ['extension', 'theme'],
        'add-ons': ['extensions', 'themes', "addon"],
        'extension': ['addon', 'theme'],
        'theme': ['addon', 'extension'],
        'awesome bar': ['address bar', 'url bar', 'location bar', 'location field', 'url field'],
        'address bar': ['awesome bar', 'url bar', 'location bar', 'location field', 'url field'],
        'url bar': ['awesome bar', 'address bar', 'location bar', 'location field', 'url field'],
        'location bar': ['awesome bar', 'address bar', 'url bar', 'location field', 'url field'],
        'location field': ['awesome bar', 'address bar', 'url bar', 'location bar', 'url field'],
        'url field': ['awesome bar', 'address bar', 'url bar', 'location bar', 'location field'],
        'bookmarks bar': ['bookmark bar', 'bookmarks toolbar', 'bookmark toolbar'],
        'bookmark bar': ['bookmarks bar', 'bookmarks toolbar', 'bookmark toolbar'],
        'bookmarks toolbar': ['bookmarks bar', 'bookmark bar', 'bookmark toolbar'],
        'bookmark toolbar': ['bookmarks bar', 'bookmark bar', 'bookmarks toolbar'],
        'home page': ['homepage', 'home screen', 'homescreen', 'awesome screen', 'firefox hub',
                      'start screen'],
        'homepage': ['home page', 'home screen', 'homescreen', 'awesome screen', 'firefox hub',
                     'start screen'],
        'home screen': ['home page', 'homepage', 'homescreen', 'awesome screen', 'firefox hub',
                        'start screen'],
        'homescreen': ['home page', 'homepage', 'home screen', 'awesome screen', 'firefox hub',
                       'start screen'],
        'awesome screen': ['home page', 'homepage', 'home screen', 'homescreen', 'firefox hub',
                           'start screen'],
        'firefox hub': ['home page', 'homepage', 'home screen', 'homescreen', 'awesome screen',
                        'start screen'],
        'start screen': ['home page', 'homepage', 'home screen', 'homescreen', 'awesome screen',
                         'firefox hub'],
        'search bar': ['search field', 'search strip', 'search box'],
        'search field': ['search bar', 'search strip', 'search box'],
        'search strip': ['search bar', 'search field', 'search box'],
        'search box': ['search bar', 'search field', 'search strip'],
        'search engine': ['search provider'],
        'search provider': ['search engine'],
        'e10s': ['multiprocess', 'multi process'],
        'multiprocess': ['e10s', 'multi process'],
        'multi process': ['e10s', 'multiprocess'],
        'two step': ['two factor', '2fa', 'authentication'],
        'two factor': ['two step', '2fa', 'authentication'],
        '2fa': ['two step', 'two factor', 'authentication'],
        'authentication': ['two step', 'two factor', '2fa'],
        'private': ['inprivate', 'incognito'],
        'inprivate': ['private', 'incognito'],
        'incognito': ['private', 'inprivate'],
        'etp': ['tracking protection', 'content blocking'],
        'tracking protection': ['etp', 'content blocking'],
        'content blocking': ['etp', 'tracking protection']
    }
