/** Shared page identity, with distinct places for facts and available actions. */
export default function PageHeading({ eyebrow, title, description, actions, summary }) {
  return (
    <header className="product-page-heading">
      <div className="product-page-heading__identity">
        {eyebrow ? <p className="product-page-heading__eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p className="product-page-heading__description">{description}</p> : null}
      </div>
      {summary ? <div className="product-page-heading__summary">{summary}</div> : null}
      {actions ? <div className="product-page-heading__actions">{actions}</div> : null}
    </header>
  );
}
