"""Readable Thai typography shared by the local desktop forms."""


def apply_theme(root):
    from tkinter import font, ttk

    available = set(font.families(root))
    family = next((name for name in ('Tahoma', 'Leelawadee UI', 'Noto Sans Thai')
                   if name in available), font.nametofont('TkDefaultFont', root=root).actual('family'))
    # Named fonts also cover classic Tk dialogs, not just ttk labels.
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont',
                 'TkCaptionFont', 'TkSmallCaptionFont', 'TkIconFont', 'TkTooltipFont'):
        font.nametofont(name, root=root).configure(family=family, size=11)
    root.configure(background='#f5f8fc')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font='TkDefaultFont', background='#f5f8fc', foreground='#183049')
    # Extra trailing space also accommodates Thai glyph shaping at label edges.
    style.configure('TLabel', padding=(0, 4, 16, 4))
    style.configure('Title.TLabel', font=(family, 20, 'bold'))
    style.configure('Section.TLabel', font=(family, 12, 'bold'))
    style.configure('TButton', padding=(12, 8))
    style.configure('TEntry', font='TkTextFont', padding=(7, 6), fieldbackground='white')
    style.configure('TNotebook.Tab', font='TkDefaultFont', padding=(18, 10))
    style.map('TNotebook.Tab', background=[('selected', '#ffffff')])
    # Fixed pixel heights clip Thai tone marks under larger Windows text scaling.
    line = font.nametofont('TkDefaultFont', root=root).metrics('linespace')
    style.configure('Treeview', font='TkDefaultFont', rowheight=line + 16,
                    background='white', fieldbackground='white')
    style.configure('Treeview.Heading', font=(family, 11, 'bold'), padding=(8, 10))
    style.map('Treeview', background=[('selected', '#dbeafe')],
              foreground=[('selected', '#123e70')])
    return family
