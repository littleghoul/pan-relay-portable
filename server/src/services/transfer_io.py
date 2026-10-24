"""Bound every streamed cache write by the declared resource size."""
def write_stream(local, blocks, total, offset=0, progress=None):
    if type(total) is not int or total < 0 or not 0 <= offset <= total:
        raise ValueError('Downloaded file size mismatch')
    if local.is_symlink():
        raise ValueError('Unsafe transfer cache file')
    with local.open('ab' if offset else 'wb') as output:
        for block in blocks:
            if len(block) > total - offset:
                raise ValueError('Downloaded file size mismatch')
            output.write(block)
            offset += len(block)
            if progress: progress('download', offset, total)
    if offset != total:
        raise ValueError('Downloaded file size mismatch')
