"""Bind authored camera endpoints to prompt prose; physical truth still needs review."""
import re
import unicodedata

VIEW_TEXT_FIELDS = ('space', 'position', 'direction', 'height', 'background')


def explicit_camera_motion(shot):
    text = shot.get('camera_text', '')
    moves = r'(?:横移到|横移至|移动到|移动至|移至|移到|绕到|绕至|转向|摇向|升至|降至|抬升至|降低至)'
    return bool(re.search(r'(?:摄影机|摄像机|镜头|机位)(?:(?:从|由)[^，。；;]{1,50}?|(?:缓慢|连续|平稳|轻微|向[东南西北]+)*)'
                          + moves, text))


def endpoint_view(shot, endpoint):
    if endpoint == 'start':
        return shot.get('view')
    if 'end_view' in shot:
        return shot['end_view']
    # A stationary view may change framing (e.g. continuous zoom), but not its subject.
    base = shot.get('view')
    subject = base.get('subject') if isinstance(base, dict) else None
    if (not explicit_camera_motion(shot) and shot.get('start_camera') == shot.get('end_camera') and
            shot.get('start_subject', subject) == shot.get('end_subject', subject)):
        return shot.get('view')
    return None


def view_error(view, visible):
    if not isinstance(view, dict):
        return 'camera view is required'
    if 'subject' not in view:
        return 'view.subject must explicitly name the subject or be null'
    for key in VIEW_TEXT_FIELDS:
        value = view.get(key)
        if not isinstance(value, str) or not value.strip() or '\n' in value or '\r' in value:
            return f'view.{key} must describe a physical setup on one line'
    subject, foreground = view.get('subject'), view.get('foreground')
    if subject is not None and (not isinstance(subject, str) or subject not in visible):
        return 'view.subject must be a visible character or null'
    if (not isinstance(foreground, list) or any(not isinstance(n, str) or n not in visible for n in foreground)
            or len(set(foreground)) != len(foreground)):
        return 'view.foreground must list unique visible relationship-only characters'
    if subject is not None and subject in foreground:
        return 'view.subject cannot be a relationship-only foreground character'
    return None


def canonical_text(value):
    value = unicodedata.normalize('NFKC', value).casefold()
    return ''.join(c for c in value if not c.isspace() and not unicodedata.category(c).startswith('P'))


def physical_key(view):
    # Camera labels, crop, focal length, foreground and background do not establish a move.
    def spatial(value):
        value = canonical_text(value)
        value = re.sub(r'([东南西北])边', r'\1侧', value)
        value = re.sub(r'的(?=[东南西北左右前后])', '', value)
        value = re.sub(r'眼睛高度|眼部高度|眼睛等高|眼部等高|眼高处', '眼高', value)
        return value
    return (canonical_text(view['space']), spatial(view['position']), spatial(view['height']))


def view_sentence(view, *, style='compact'):
    subject = f'主拍{view["subject"]}' if view['subject'] is not None else '此构图无主拍人物'
    foreground = ('，' + '、'.join(view['foreground']) + '仅作前景关系参照') if view['foreground'] else ''
    if style == 'compact':
        foreground = ('，前景' + '、'.join(view['foreground'])) if view['foreground'] else ''
        return (f'{view["space"]}，{view["position"]}，朝{view["direction"]}，{view["height"]}；'
                f'{subject}{foreground}，背景{view["background"]}。')
    if style == 'expanded':
        foreground = ('，前景为' + '、'.join(view['foreground'])) if view['foreground'] else ''
        return (f'摄影机在{view["space"]}的{view["position"]}，朝向{view["direction"]}，高度为{view["height"]}；'
                f'{subject}{foreground}，背景为{view["background"]}。')
    return (f'摄影机位于{view["space"]}的{view["position"]}，朝向{view["direction"]}，'
            f'机位高度为{view["height"]}；{subject}{foreground}，背景参照为{view["background"]}。')


def camera_view_prose(shot, *, style='compact'):
    if 'view' not in shot and 'end_view' not in shot:
        return ''
    start, end = endpoint_view(shot, 'start'), endpoint_view(shot, 'end')
    for endpoint, view in [('start', start), ('end', end)]:
        error = view_error(view, shot.get('visible_characters', []))
        if error:
            raise ValueError(f'{endpoint}: {error}')
    if start == end:
        return view_sentence(start, style=style)
    if style == 'compact':
        labels = {'space': '场景', 'position': '机位', 'direction': '朝向', 'height': '高度',
                  'subject': '主拍', 'foreground': '前景', 'background': '背景'}
        changes = []
        for field, label in labels.items():
            if start[field] != end[field]:
                value = end[field]
                if isinstance(value, list):
                    value = '、'.join(value) or '无'
                elif value is None:
                    value = '无人物主拍'
                changes.append(label + str(value))
        return '起镜：' + view_sentence(start, style=style) + '落幅：' + '，'.join(changes) + '。'
    return '起始时，' + view_sentence(start, style=style) + '结束时，' + view_sentence(end, style=style)


def validate_views(shot, label, errors):
    if explicit_camera_motion(shot) and 'end_view' not in shot:
        errors.append(f'{label}: explicit camera motion requires end_view even when camera IDs are unchanged')
    if 'view' not in shot and 'end_view' not in shot:
        return
    for endpoint in ('start', 'end'):
        view = endpoint_view(shot, endpoint)
        error = view_error(view, shot.get('visible_characters', []))
        if error:
            errors.append(f'{label}/{endpoint}: {error}')
            continue
        declared_subject = shot.get(endpoint + '_subject')
        if declared_subject is not None and declared_subject != view['subject']:
            errors.append(f'{label}/{endpoint}: primary character differs from camera view')


def check_view_binding(shot, camera_values, label, errors):
    if 'end_view' not in shot and any(explicit_camera_motion({'camera_text': value}) for value in camera_values):
        errors.append(f'{label}: final camera motion requires end_view; start view cannot stand for its endpoint')
    if 'view' not in shot and 'end_view' not in shot:
        return
    try:
        evidence = camera_view_prose(shot)
    except ValueError as exc:
        errors.append(f'{label}: {exc}')
        return
    normalize = lambda value: ' '.join(value.split())
    alternatives = (evidence, camera_view_prose(shot, style='expanded'), camera_view_prose(shot, style='legacy'))
    if not any(normalize(candidate) in normalize(value) for candidate in alternatives for value in camera_values):
        errors.append(f'{label}: camera view evidence is absent from this final camera interval')
